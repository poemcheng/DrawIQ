"""Real modal sign-in lifecycle and startup failures, using temporary workspaces."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialog
from workbench.auth import AuthService
from workbench.auth_ui import DesktopSession, LoginDialog
from workbench.startup import desktop_environment
from workbench.harness_validation import source_fingerprint

PASSWORD = 'Temporary startup verification 2026!'


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='drawiq_launch_test_')
        self.root = Path(self.temporary.name)/'workspace'
        self.controller = DesktopSession(self.app,self.root)

    def tearDown(self):
        if self.controller.login is not None:
            self.controller.login.reject()
        if self.controller.window is not None:
            self.controller.window.close()
        self.app.processEvents()
        self.temporary.cleanup()

    def test_fresh_sign_in_is_visible_and_cancel_exits_without_opening_documents(self):
        observed = {}
        def inspect():
            dialog = self.controller.login
            observed.update(visible=dialog.isVisible(),setup=dialog.setup,can_setup=dialog.admin_setup_button.isVisible(),
                in_screen=self.app.primaryScreen().availableGeometry().intersects(dialog.frameGeometry()))
            dialog.reject()
        QTimer.singleShot(25,inspect)
        with patch('workbench.auth_ui.QMessageBox.critical') as error, \
                patch('workbench.store.Store.list_documents',side_effect=AssertionError('login must not read documents')), \
                redirect_stdout(io.StringIO()) as log:
            self.controller.start()
        self.assertEqual(observed,{'visible':True,'setup':False,'can_setup':True,'in_screen':True})
        self.assertIsNone(self.controller.window)
        self.assertIn('login.visible',log.getvalue())
        self.assertIn('login.closed',log.getvalue())
        error.assert_not_called()

    def test_existing_account_real_modal_submit_opens_authenticated_window(self):
        self.controller.auth.bootstrap(PASSWORD,must_change=False)
        observed = {}
        def login():
            dialog = self.controller.login
            observed.update(visible=dialog.isVisible(),setup=dialog.setup)
            dialog.username.setText('admin'); dialog.password.setText(PASSWORD); dialog.submit()
        QTimer.singleShot(25,login)
        with patch('workbench.auth_ui.QMessageBox.critical') as error, redirect_stdout(io.StringIO()) as log:
            self.controller.start()
        self.app.processEvents()
        self.assertEqual(observed,{'visible':True,'setup':False})
        self.assertTrue(self.controller.window.isVisible())
        self.assertIsNone(self.controller.window.doc)
        self.controller.auth.require(self.controller.window.auth_session)
        self.assertIn('workspace.visible',log.getvalue())
        error.assert_not_called()

    def test_modal_initialization_failure_shows_error_and_exits_instead_of_silent_timer_failure(self):
        with patch('workbench.auth_ui.LoginDialog',side_effect=RuntimeError('isolated startup error')), \
                patch('workbench.auth_ui.QMessageBox.critical') as error, \
                patch.object(self.app,'exit') as exit_app, \
                patch('workbench.auth_ui.failure') as diagnostic:
            self.controller.start()
        diagnostic.assert_called_once_with('desktop.failed')
        self.assertIn('isolated startup error',error.call_args.args[2])
        exit_app.assert_called_once_with(1)
        self.assertIsNone(self.controller.login)

    def test_desktop_environment_removes_foreign_qt_plugins_but_preserves_other_settings(self):
        with patch.dict(os.environ,{'QT_QPA_PLATFORM':'offscreen','QT_PLUGIN_PATH':'foreign-qt',
                'QT_QPA_PLATFORM_PLUGIN_PATH':'foreign-platforms','DRAWIQ_DATA_ROOT':'keep-data'}), \
                patch('workbench.startup.sys.platform','win32'), redirect_stdout(io.StringIO()):
            desktop_environment()
            self.assertNotIn('QT_QPA_PLATFORM',os.environ)
            self.assertNotIn('QT_PLUGIN_PATH',os.environ)
            self.assertNotIn('QT_QPA_PLATFORM_PLUGIN_PATH',os.environ)
            self.assertEqual(os.environ['DRAWIQ_DATA_ROOT'],'keep-data')

    def test_entrypoint_and_launcher_changes_invalidate_harness_fingerprint(self):
        root=Path(self.temporary.name)/'fingerprint'; root.mkdir()
        for name in ('app.py','run.bat'):
            (root/name).write_text('original',encoding='utf-8')
        before=source_fingerprint(root)
        self.assertIn('app.py',before['files']); self.assertIn('run.bat',before['files'])
        (root/'run.bat').write_text('changed',encoding='utf-8')
        self.assertNotEqual(before,source_fingerprint(root))


if __name__=='__main__':
    unittest.main()
