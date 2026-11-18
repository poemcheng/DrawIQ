"""Exercise the Windows launchers with real child interpreters, without GUI or production DB writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


APP_ROOT=Path(__file__).resolve().parents[1]
from workbench import __version__
ROOT_WRAPPER=APP_ROOT/'tools'/'desktop_launch_fixture.bat'


@unittest.skipUnless(os.name=='nt','Windows batch launcher integration')
class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime_temp=tempfile.TemporaryDirectory(prefix='drawiq-launch-runtime-')
        cls.runtime=Path(cls.runtime_temp.name)/'venv with spaces'
        subprocess.run([sys.executable,'-m','venv','--without-pip','--system-site-packages',str(cls.runtime)],
                       check=True,capture_output=True,text=True,timeout=45)

    @classmethod
    def tearDownClass(cls):
        cls.runtime_temp.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='drawiq launch test ')
        self.root=Path(self.temp.name)
        self.app=self.root/'DrawIQ_Workbench_0.11.0'
        self.app.mkdir()
        self.wrapper=self.root/ROOT_WRAPPER.name
        shutil.copy2(ROOT_WRAPPER,self.wrapper)
        shutil.copy2(APP_ROOT/'run.bat',self.app/'run.bat')
        package=self.app/'workbench'
        package.mkdir()
        (package/'__init__.py').write_text('',encoding='utf-8')
        (package/'ui.py').write_text('# Import-only isolated startup fixture.\n',encoding='utf-8')
        (self.app/'app.py').write_text(
            'import json,os,sys\nfrom pathlib import Path\n'
            'Path("child.json").write_text(json.dumps({"argv":sys.argv[1:],"python":sys.executable}),encoding="utf-8")\n'
            'print("isolated child executed",flush=True)\n'
            'raise SystemExit(int(os.environ.get("DRAWIQ_TEST_CHILD_EXIT","0")))\n',encoding='utf-8')
        self.env=os.environ.copy()
        self.env['PYTHONIOENCODING']='utf-8'

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self,*args,env=None,wrapper=None):
        # list2cmdline quotes the real batch path and CLI arguments, including spaces.
        command=subprocess.list2cmdline(['call',str(wrapper or self.wrapper),*args])
        # Pass cmd's command tail verbatim: Python's argv escaping would add
        # backslashes to embedded quotes that cmd does not interpret as escapes.
        invocation='"'+os.environ.get('COMSPEC','cmd.exe')+'" /d /s /c "'+command+'"'
        return subprocess.run(invocation,
                              cwd=self.root,env=env or self.env,input='\n',capture_output=True,
                              encoding='utf-8',errors='replace',timeout=30)

    def install_venv(self):
        shutil.copytree(self.runtime,self.app/'.venv')
        return self.app/'.venv'/'Scripts'/'python.exe'

    def child(self):
        return json.loads((self.app/'child.json').read_text(encoding='utf-8'))

    def test_wrapper_forwards_quoted_cli_arguments(self):
        result=self.invoke('--data',str(self.root/'隔離 資料'),'--help')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.child()['argv'],['--data',str(self.root/'隔離 資料'),'--help'])
        self.assertIn('[launcher] Python:',result.stdout)
        self.assertIn('Desktop imports passed.',result.stdout)

    def test_private_runtime_precedes_path_python(self):
        private=self.install_venv()
        result=self.invoke('--help')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(Path(self.child()['python']).resolve(),private.resolve())
        self.assertNotIn('Preferred runtime failed.',result.stdout)

    def test_failed_private_runtime_falls_back_to_py_with_diagnostics(self):
        self.install_venv()
        # This real import hook affects only the copied venv, leaving py -3 usable.
        (self.app/'sitecustomize.py').write_text(
            'import importlib.abc,sys\n'
            'class FailPrivateQt(importlib.abc.MetaPathFinder):\n'
            ' def find_spec(self,fullname,path,target=None):\n'
            '  if sys.prefix!=sys.base_prefix and fullname=="PyQt6":\n'
            '   raise ImportError("controlled private runtime missing Qt")\n'
            'sys.meta_path.insert(0,FailPrivateQt())\n',encoding='utf-8')
        self.env['PYTHONPATH']=str(self.app)
        result=self.invoke('--help')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertNotIn('.venv',self.child()['python'])
        log=(self.app/'launcher.log').read_text(encoding='utf-8')
        self.assertIn('controlled private runtime missing Qt',log)
        self.assertIn('Trying the Python launcher: py -3.',log)
        self.assertIn('isolated child executed',log)

    def test_both_preflights_fail_without_running_application(self):
        (self.app/'PyQt6.py').write_text('raise ImportError("controlled missing desktop dependencies")\n',encoding='utf-8')
        result=self.invoke('--help')
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)
        self.assertFalse((self.app/'child.json').exists())
        log=(self.app/'launcher.log').read_text(encoding='utf-8')
        self.assertEqual(log.count('ImportError: controlled missing desktop dependencies'),2)
        self.assertIn('could not find a Python runtime',result.stdout)
        self.assertNotIn('install.bat',result.stdout)

    def test_child_failure_preserves_exit_code_and_diagnostic(self):
        self.env['DRAWIQ_TEST_CHILD_EXIT']='7'
        result=self.invoke('--help')
        self.assertEqual(result.returncode,7,result.stdout+result.stderr)
        self.assertIn('isolated child executed',result.stdout)
        self.assertEqual(self.child()['argv'],['--help'])

    def test_real_root_help_stays_cli_without_creating_data(self):
        data=self.root/'must not be created'
        result=self.invoke('--data',str(data),'--help',wrapper=APP_ROOT/'run.bat')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('DrawIQ engineering evidence workbench',
                      (APP_ROOT/'launcher.log').read_text(encoding='utf-8'))
        self.assertFalse(data.exists())


if __name__=='__main__':
    unittest.main()
