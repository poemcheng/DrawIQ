import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from openpyxl import load_workbook
from tests.test_workbench import Fixture
from workbench.domain import evidence,requirement,digest
from workbench.knowledge import summary,build_graph,CATEGORIES
from workbench.corpus import records_for_document,export_corpus,fingerprint
from workbench.business import BusinessService,calculate_quote,COST_FIELDS
from workbench.store import ConflictError
from workbench.retrieval import build_index,index_status,search,engineering_matches
from workbench.ingest import sha256


class SummaryCorpusTests(Fixture):
    def with_semantics(self):
        d=self.store.get(self.doc['id'])
        for text in ['塗膠長：9牙以上，未塗膠長：2全牙以下','(最高工作溫度可達135°C)','註3.數量：130萬PCS/年','首批需附膠種成分證明，每批需附檢測報告','請建議符合規範之膠種']:
            d=self.store.save_region(d['id'],d['version'],evidence(1,[.1,.3,.9,.4],text),None,'synthetic evidence')
        return d

    def test_engineering_summary_refines_scope_without_changing_source(self):
        d=self.with_semantics(); before=self.store.get(d['id']); s=summary(d)
        bykey={f['key']:f for f in s['facts']}
        self.assertEqual(bykey['coating.minimum_thread_count']['operator'],'ge')
        self.assertEqual(bykey['coating.minimum_thread_count']['value'],'9')
        self.assertEqual(bykey['coating.maximum_uncoated_full_threads']['value'],'2')
        self.assertEqual(bykey['temperature.capability']['unit'],'°C')
        self.assertEqual(bykey['adhesive.selection']['state'],'pending_decision')
        self.assertEqual(bykey['document.first_batch']['scope'],'首批')
        self.assertEqual(bykey['document.every_batch']['scope'],'每批')
        self.assertIn('非本次訂購量',bykey['demand.annual']['label'])
        self.assertEqual(self.store.get(d['id']),before)

    def test_parser_noise_is_flagged_and_temperature_not_an_angle(self):
        d=self.with_semantics(); e=next(e for e in d['regions'] if '135' in e['text'])
        r=requirement('dimension.角度','135°',evidence_ids=[e['id']])
        d=self.store.add_requirement(d['id'],d['version'],r,'test wrong category')
        s=summary(d); f=next(f for f in s['facts'] if f['requirement_id']==r['id'])
        self.assertFalse(f['summary_visible']); self.assertTrue(f['quality_flags'])
        self.assertTrue(any(row['record_type']=='structured' and row['requirement_id']==r['id'] for row in records_for_document(d)))

    def test_graph_has_no_dangling_edges_and_has_provenance(self):
        d=self.with_semantics(); g=build_graph([d]); ids={n['id'] for n in g['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in g['edges']))
        self.assertTrue(any(e['relation']=='supported_by' for e in g['edges']))
        self.assertFalse(any(n['type']=='Assertion' for n in build_graph([d],approved_only=True)['nodes']))

    def test_unreviewed_corpus_exports_now_but_no_fake_sft(self):
        self.with_semantics(); result=export_corpus(self.store)
        with zipfile.ZipFile(result['corpus_bundle']) as z:
            manifest=json.loads(z.read('manifest.json')); self.assertEqual(manifest['statistics']['reviewed_sft_samples'],0)
            self.assertEqual(z.read('sft_reviewed.jsonl'),b''); self.assertTrue(z.read('corpus_pretraining.jsonl'))
            raw=[json.loads(line) for line in z.read('corpus_raw.jsonl').decode().splitlines()]
            self.assertTrue(all(r['split']=='unassigned' and not r['supervised_eligible'] for r in raw))
            self.assertTrue(all(__import__('hashlib').sha256(z.read(n)).hexdigest()==h for n,h in manifest['files'].items()))
            wb=load_workbook(io.BytesIO(z.read('corpus_catalog.xlsx'))); self.assertGreater(wb['語料總覽'].max_row,1); wb.close()

    def test_approved_supervision_disappears_after_edit(self):
        d=self.approved(); records=records_for_document(d); self.assertEqual(sum(r['supervised_eligible'] for r in records),1)
        d=self.store.edit_requirement(d['id'],d['version'],self.req['id'],{'value':'10'},'test correction')
        self.assertEqual(sum(r['supervised_eligible'] for r in records_for_document(d)),0)


class BusinessTests(Fixture):
    def ready_case(self):
        service=BusinessService(self.store); c=service.get(self.doc['id']); c['rfq']['quantity']='100'
        c['quote'].update(currency='TWD',unit_costs={k:'1' for k in COST_FIELDS},setup_cost='100',margin_percent='20')
        return service,c

    def test_quote_uses_order_quantity_and_correct_margin_math(self):
        service,c=self.ready_case(); result=calculate_quote(c)
        self.assertEqual(result['total_cost'],'600'); self.assertEqual(result['unit_quote'],'7.5000'); self.assertEqual(result['total_quote'],'750.00')
        c['rfq']['quantity']=''
        with self.assertRaises(ValueError): calculate_quote(c)

    def test_missing_costs_are_not_silently_zero(self):
        service,c=self.ready_case(); c['quote']['unit_costs']['material']=''
        with self.assertRaises(ValueError): calculate_quote(c)
        for bad in ('NaN','Infinity','-1'):
            c['quote']['unit_costs']['material']=bad
            with self.assertRaises(ValueError): calculate_quote(c)
            with self.assertRaises(ValueError): service.save(c)

    def test_business_versions_and_drawing_change_block_stale_plan(self):
        service,c=self.ready_case(); c=service.save(c)
        with self.assertRaises(ConflictError): service.save({**c,'version':0})
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'10'},'changed')
        with self.assertRaises(ConflictError): service.save(c)
        with self.assertRaises(ValueError): service.export(d['id'])
        c=service.rebase(d['id']); self.assertEqual(c['source_version'],d['version'])
        self.assertEqual(c['rfq']['quantity'],'100')

    def test_process_requires_valid_knowledge_and_approval_for_execution(self):
        service,c=self.ready_case(); c['operations'][0]['evidence_fact_ids']=['missing']
        with self.assertRaises(ValueError): service.save(c)
        c['operations'][0]['evidence_fact_ids']=[]; c['operations'][0]['status']='進行中'
        with self.assertRaises(ValueError): service.save(c)
        c['operations'][0]['status']='已排程'; service.save(c)

    def test_export_is_draft_with_source_and_no_invented_price(self):
        service=BusinessService(self.store); path=service.export(self.doc['id'])
        with zipfile.ZipFile(path) as z:
            data=json.loads(z.read('business.json')); self.assertEqual(data['export_status'],'draft'); self.assertIsNone(data['quote_calculation'])
            self.assertIn('original.pdf',z.namelist()); self.assertIn('business_workbook.xlsx',z.namelist())

    def test_rebase_removes_deleted_links_and_preserves_history(self):
        service,c=self.ready_case(); fid=summary(self.store.get(self.doc['id']))['facts'][0]['id']
        c['operations'][0]['evidence_fact_ids']=[fid]; service.save(c)
        self.store.delete_requirement(self.doc['id'],1,self.req['id'],'synthetic removal')
        rebased=service.rebase(self.doc['id']); self.assertEqual(rebased['operations'][0]['evidence_fact_ids'],[])
        with zipfile.ZipFile(service.export(self.doc['id'])) as z:
            history=json.loads(z.read('business_history.json'))
            self.assertEqual(history[-1]['before']['operations'][0]['evidence_fact_ids'],[fid])


class KnowledgeApiTests(Fixture):
    def test_auth_candidate_summary_and_approved_graph_defaults(self):
        from fastapi.testclient import TestClient
        from workbench.api import create_app
        token='synthetic-test-token-not-real-1234'; client=TestClient(create_app(self.store.root,token)); headers={'Authorization':'Bearer '+token}
        for route in ('/semantic/status','/knowledge/graph',f'/documents/{self.doc["id"]}/summary'):
            self.assertEqual(client.get(route).status_code,401)
        self.assertEqual(client.get('/knowledge/graph',headers=headers).json()['nodes'],[])
        graph=client.get('/knowledge/graph?include_candidates=true',headers=headers).json()
        self.assertTrue(any(n['type']=='Assertion' for n in graph['nodes']))
        s=client.get(f'/documents/{self.doc["id"]}/summary',headers=headers).json()
        self.assertEqual(s['document_id'],self.doc['id']); self.assertEqual(s['status'],'needs_review')
        self.assertEqual(client.get('/semantic/search?q=coating',headers=headers).status_code,409)
        with patch('workbench.retrieval.search',return_value=[]) as find:
            self.assertEqual(client.get('/semantic/search?q=coating',headers=headers).status_code,200)
            self.assertTrue(find.call_args.kwargs['approved_only'])


class FakeTokenizer:
    def __call__(self,text,**kwargs):
        return {'input_ids':list(range(len(text))),'offset_mapping':[(i,i+1) for i in range(len(text))]}

class FakeEncoder:
    max_seq_length=128; tokenizer=FakeTokenizer()
    def encode(self,texts,**kwargs):
        return np.array([[1.,0.,0.] if 'COATING' in t else [0.,1.,0.] for t in texts],dtype=np.float32)

class IndexTests(Fixture):
    def test_temperature_and_full_thread_terms_disambiguate_ranking(self):
        query='需要耐高溫的塗膠要求'
        self.assertGreater(engineering_matches(query,'塗膠 工作溫度 135°C')[0],engineering_matches(query,'塗膠360°')[0])
        query='螺紋 M4X0.7-6g 的要求'
        self.assertGreater(engineering_matches(query,'螺紋 M4×0.7-6g')[0],engineering_matches(query,'螺紋 M4')[0])
        self.assertEqual(engineering_matches('M4','M42')[0],0)

    def test_index_excludes_examples_and_refuses_stale_results(self):
        model=FakeEncoder()
        with patch('workbench.retrieval.load_encoder',return_value=model),patch('workbench.retrieval.model_info',return_value={'test':True}):
            build_index(self.store)
        self.assertEqual(index_status(self.store)['status'],'ready')
        hits=search(self.store,'COATING',encoder=model); self.assertTrue(hits)
        self.assertEqual(search(self.store,'COATING',approved_only=True,encoder=model),[])
        d=self.store.edit_requirement(self.doc['id'],1,self.req['id'],{'value':'10'},'changed')
        self.assertEqual(index_status(self.store)['status'],'stale')
        with self.assertRaises(ValueError): search(self.store,'COATING',encoder=model)

    def test_corrupt_index_rejected(self):
        model=FakeEncoder()
        with patch('workbench.retrieval.load_encoder',return_value=model),patch('workbench.retrieval.model_info',return_value={'test':True}): result=build_index(self.store)
        path=Path(result['semantic_index'])/'records.json'; path.write_text('[]',encoding='utf-8')
        with self.assertRaises(ValueError): search(self.store,'COATING',encoder=model)

    def test_cancel_leaves_previous_index_intact(self):
        model=FakeEncoder()
        with patch('workbench.retrieval.load_encoder',return_value=model),patch('workbench.retrieval.model_info',return_value={'test':True}):
            build_index(self.store); before=(self.store.root/'semantic_index/current.json').read_bytes()
            with self.assertRaises(ValueError): build_index(self.store,cancelled=lambda:True)
        self.assertEqual((self.store.root/'semantic_index/current.json').read_bytes(),before)
