import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from tools.run_quote_skill import main


class SkillCliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.source=self.root/'input'; self.source.mkdir()
        self.output=self.root/'output'

    def write(self,name='a',**extra):
        doc={'document_id':name,'document_type':'quote_label','pages':[{'page':1,'width':1000,'height':1000,
             'lines':[{'text':'材質：S45C','bbox':[10,10,200,40],'confidence':.99}]}],**extra}
        path=self.source/(name+'.json'); path.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
        return path

    def run_cli(self,path):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return main(['--input',str(path),'--output',str(self.output)])

    def test_batch_exports_results_review_queue_and_raw_source(self):
        self.write('a'); self.write('b'); self.assertEqual(self.run_cli(self.source),0)
        result=[json.loads(x) for x in (self.output/'results.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual({x['document_id'] for x in result},{'a','b'})
        self.assertEqual(result[0]['fields']['material']['normalized'],'S45C')
        self.assertTrue((self.output/'review_queue.jsonl').is_file())

    def test_invalid_batch_leaves_previous_exports_intact(self):
        self.write(); self.assertEqual(self.run_cli(self.source),0)
        before={p.relative_to(self.output):p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        (self.source/'bad.json').write_text('{"pages":[]}',encoding='utf-8')
        self.assertEqual(self.run_cli(self.source),2)
        self.assertEqual(before,{p.relative_to(self.output):p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
