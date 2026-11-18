"""Source validation, existing human decisions, and deterministic export failures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workbench.domain import evidence,requirement
from workbench.ocr_contract import from_ocr_json
from workbench.skill_records import SCHEMA_VERSION,build_record,export_records


def document(doc_id='source_01'):
    e=evidence(1,[.1,.2,.8,.25],'材質： S45C','RapidOCR')
    e['id']='ev_material'; e['ocr_confidence']=.97
    return {'id':doc_id,'version':3,'source_sha256':'a'*64,'status':'needs_review',
            'pages':[{'number':1,'width':1000,'height':1400}],
            'regions':[e],'requirements':[],'tool_runs':[]}


def skill_field(key='material',raw='S45C',normalized='S45C',*,source_id='ev_material',status='extracted',issues=None):
    return {'key':key,'label':'材質','raw':raw,'normalized':normalized,
            'sources':[{'region_id':source_id,'text':'GENERATED TEXT THAT MUST NOT CERTIFY ITSELF',
                        'page':99,'box':[0,0,1,1],'method':'generated','confidence':1}],
            'confidence':1,'status':status,'issues':issues or [],'alternatives':[]}


def report(*fields,dtype='quote_label',specs=None):
    return {'document_type':dtype,'fields':list(fields),'issues':[],
            'specification_candidates':specs or []}


class SkillRecordTests(unittest.TestCase):
    def build(self,doc,result):
        with patch('workbench.skill_records._skill_report',return_value=result):
            return build_record(doc)

    def test_source_is_rebuilt_from_actual_region_and_pixels(self):
        doc=document(); before=copy.deepcopy(doc)
        record=self.build(doc,report(skill_field()))
        field=record['fields']['material']; source=field['source']
        self.assertEqual(source['text'],'材質： S45C')
        self.assertEqual(source['page'],1); self.assertEqual(source['bbox'],[100,280,800,350])
        self.assertEqual(source['box'],[.1,.2,.8,.25]); self.assertEqual(source['method'],'RapidOCR')
        self.assertEqual(field['confidence'],.97)
        self.assertFalse(field['human_reviewed']); self.assertEqual(doc,before)

    def test_current_manual_value_and_verified_status_are_not_overwritten(self):
        doc=document(); doc['regions'][0]['text']='材質： S45C / 人工確認 S50C'
        r=requirement('material','S50C',evidence_ids=['ev_material'],origin='quote_skill',
                      status='verified',support_verified=True,raw_value='S45C')
        r['id']='manual_material'; doc['requirements']=[r]; doc['quotation_mapping']={'material':r['id']}
        original=copy.deepcopy(doc)
        field=self.build(doc,report(skill_field()))['fields']['material']
        self.assertEqual(field['raw'],'S50C'); self.assertEqual(field['normalized'],'S50C')
        self.assertEqual(field['selected_value'],'S50C'); self.assertEqual(field['original_raw'],'S45C')
        self.assertTrue(field['human_reviewed']); self.assertTrue(field['support_verified'])
        self.assertEqual(field['review_status'],'verified'); self.assertEqual(field['confidence'],.97)
        self.assertEqual(doc,original)

    def test_existing_engineering_thread_gets_typed_fallback_without_rule_field(self):
        doc=document(); doc['regions'][0]['text']='Full Thread M6x1,0-6g'
        r=requirement('thread.specification','M6x1,0-6g',label='螺紋規格',evidence_ids=['ev_material'],
                      origin='engineering_catalogue',scope='page:1')
        doc['requirements']=[r]
        field=self.build(doc,report(dtype='engineering_drawing'))['fields']['product.specification']
        self.assertEqual(field['raw'],'M6x1,0-6g'); self.assertEqual(field['selected_value'],'M6x1,0-6g')
        self.assertEqual(field['normalized']['diameter_mm'],6)
        self.assertEqual(field['normalized']['pitch_mm'],1); self.assertEqual(field['normalized']['tolerance'],'6g')
        self.assertIsNone(field['normalized']['length_value'])
        self.assertEqual(field['requirement_context']['origin'],'engineering_catalogue')

    def test_engineering_length_unit_is_not_assumed_by_fallback(self):
        doc=document(); doc['regions'][0]['text']='螺紋規格： M8x1.25x50'
        r=requirement('product.specification','M8x1.25x50',evidence_ids=['ev_material'])
        doc['requirements']=[r]; doc['quotation_mapping']={'product.specification':r['id']}
        field=self.build(doc,report(dtype='engineering_drawing'))['fields']['product.specification']
        self.assertEqual(field['normalized']['length_value'],50); self.assertIsNone(field['normalized']['length_unit'])
        self.assertIn('length_unit_unverified',field['issues']); self.assertEqual(field['status'],'needs_review')

    def test_unverified_vlm_source_cannot_become_machine_certainty(self):
        doc=document(); doc['regions'][0]['source']='VLM:local-model'
        r=requirement('material','S50C',evidence_ids=['ev_material'])
        doc['requirements']=[r]; doc['quotation_mapping']={'material':r['id']}
        field=self.build(doc,report(skill_field()))['fields']['material']
        self.assertEqual(field['raw'],'S50C'); self.assertEqual(field['status'],'needs_review')
        self.assertIn('unverified_model_source',field['issues']); self.assertFalse(field['human_reviewed'])

    def test_manual_clear_stays_null_even_if_rule_has_value(self):
        doc=document(); doc['quotation_mapping']={'material':None}
        field=self.build(doc,report(skill_field()))['fields']['material']
        self.assertIsNone(field['raw']); self.assertIsNone(field['normalized'])
        self.assertEqual(field['status'],'missing'); self.assertIn('manual_source_cleared',field['issues'])

    def test_invalid_reference_never_exports_fake_evidence(self):
        field=self.build(document(),report(skill_field(source_id='invented')))['fields']['material']
        self.assertEqual(field['sources'],[]); self.assertIsNone(field['source'])
        self.assertIsNone(field['confidence']); self.assertEqual(field['status'],'needs_review')
        self.assertIn('invalid_source_reference',field['issues'])
        self.assertEqual(field['invalid_source_ids'],['invented'])

    def test_conflicting_alternatives_keep_every_original_source(self):
        doc=document(); second=evidence(1,[.1,.3,.8,.35],'材質： SUS304','PDF文字層')
        second['id']='ev_second'; second['ocr_confidence']=1; doc['regions'].append(second)
        first=skill_field(); other=skill_field(raw='SUS304',normalized='SUS304',source_id='ev_second')
        conflict=skill_field(raw=None,normalized=None,status='conflict')
        conflict['sources']+=other['sources']; conflict['alternatives']=[first,other]
        field=self.build(doc,report(conflict))['fields']['material']
        self.assertEqual(field['status'],'conflict'); self.assertIsNone(field['raw'])
        self.assertEqual([a['raw'] for a in field['alternatives']],['S45C','SUS304'])
        self.assertEqual([s['region_id'] for s in field['sources']],['ev_material','ev_second'])

    def test_low_score_is_not_changed_by_human_review(self):
        doc=document(); doc['regions'][0]['ocr_confidence']=.5
        r=requirement('material','S45C',evidence_ids=['ev_material'],status='verified',support_verified=True)
        doc['requirements']=[r]; doc['quotation_mapping']={'material':r['id']}
        field=self.build(doc,report(skill_field()))['fields']['material']
        self.assertEqual(field['confidence'],.5); self.assertTrue(field['human_reviewed'])
        self.assertIn('low_engine_confidence',field['issues'])

    def test_one_missing_source_score_does_not_become_a_known_confidence(self):
        doc=document(); extra=evidence(1,[.1,.3,.8,.35],'S45C','manual')
        extra['id']='ev_no_score'; doc['regions'].append(extra)
        item=skill_field(); item['sources'].append({'region_id':'ev_no_score'})
        field=self.build(doc,report(item))['fields']['material']
        self.assertIsNone(field['confidence']); self.assertEqual(field['sources'][0]['confidence'],.97)
        self.assertIsNone(field['sources'][1]['confidence'])

    def test_provided_text_without_bbox_or_dimensions_stays_unlocated(self):
        doc=from_ocr_json({'document_id':'manual_transcription','document_type':'quote_label',
                          'pages':[{'page':1,'lines':[{'text':'材質： S45C'}]}]})
        line=doc['unlocated_ocr'][0]
        field=self.build(doc,report(skill_field(source_id=line['id'])))['fields']['material']
        source=field['source']; self.assertEqual(source['text'],'材質： S45C')
        self.assertIsNone(source['box']); self.assertIsNone(source['bbox']); self.assertFalse(source['located'])
        self.assertIsNone(field['confidence']); self.assertIn('missing_bbox',field['issues'])
        self.assertIn('page_dimensions_missing',field['issues'])
        record=self.build(doc,report(skill_field(source_id=line['id'])))
        self.assertFalse(record['source_contract']['ocr_executed'])
        self.assertEqual(record['source_hash_kind'],'provided_ocr_json_snapshot')

    def test_pixel_bbox_survives_without_inventing_page_size(self):
        doc=from_ocr_json({'document_id':'pixel_unknown_size','document_type':'quote_label',
                          'pages':[{'page':1,'lines':[{'text':'材質： S45C','bbox':[10,20,300,40],'confidence':.95}]}]})
        line=doc['unlocated_ocr'][0]
        field=self.build(doc,report(skill_field(source_id=line['id'])))['fields']['material']
        self.assertEqual(field['source']['bbox'],[10,20,300,40]); self.assertIsNone(field['source']['box'])
        self.assertEqual(field['confidence'],.95); self.assertEqual(field['status'],'needs_review')

    def test_missing_price_is_null_with_raw_unit_fragment_preserved(self):
        doc=document(); doc['regions'][0]['text']='單價： / TWD / M'
        item=skill_field('quotation.price','/ TWD / M',None,status='missing',issues=['blank_price','unit_unverified'])
        field=self.build(doc,report(item))['fields']['quotation.price']
        self.assertIsNone(field['raw']); self.assertIsNone(field['normalized'])
        self.assertEqual(field['original_raw'],'/ TWD / M'); self.assertEqual(field['status'],'missing')

    def test_unanchored_geometry_is_candidate_not_confirmed_specification(self):
        doc=document(); doc['regions'][0]['text']='M8x1.25-6h'
        item=skill_field('product.specification','M8x1.25-6h',{'diameter_mm':8,'pitch_mm':1.25,'tolerance':'6h'},status='needs_review')
        field=self.build(doc,report(dtype='engineering_drawing',specs=[item]))['fields']['engineering.specification']
        self.assertEqual(field['status'],'needs_review'); self.assertFalse(field['human_reviewed'])
        self.assertIn('engineering_object_and_units_unverified',field['issues'])


class ProposalRecordTests(unittest.TestCase):
    def build(self,doc,row,result=None):
        with patch('workbench.skill_records._skill_report',return_value=result or report(skill_field())), \
             patch('workbench.skill_records.quotation_rows',return_value=[row]) as resolver:
            record=build_record(doc)
        resolver.assert_called_once_with(doc,include_proposals=True)
        return record

    def row(self,source,**values):
        return {'key':'material','label':'材質','source':source,'automatic':False,
                'status':'建議／待確認','proposed':True,'candidate_ids':[source['id']] if source else [],
                'proposal_score':87.3,'proposal_confidence':.91,'proposal_reason':'explicit_material_label',
                'proposal_issues':[],**values}

    def test_low_confidence_proposal_retains_real_source_and_distinct_rank_score(self):
        doc=document(); doc['regions'][0]['ocr_confidence']=.5
        selected=requirement('material','S45C',evidence_ids=['ev_material'])
        doc['requirements']=[selected]; before=copy.deepcopy(doc)
        field=self.build(doc,self.row(selected,proposal_issues=['low_confidence']))['fields']['material']
        self.assertEqual(field['raw'],'S45C'); self.assertEqual(field['confidence'],.5)
        self.assertEqual(field['selected_proposal']['score'],87.3)
        self.assertEqual(field['selected_proposal']['confidence'],.91)
        self.assertEqual(field['selected_proposal']['chosen_requirement_id'],selected['id'])
        self.assertEqual(field['selected_proposal']['reason'],'explicit_material_label')
        self.assertEqual(field['source']['text'],'材質： S45C')
        self.assertTrue(field['proposed']); self.assertFalse(field['automatic'])
        self.assertFalse(field['human_reviewed']); self.assertEqual(field['status'],'needs_review')
        self.assertIn('low_confidence',field['proposal_issues']); self.assertEqual(doc,before)

    def test_conflict_selects_proposal_without_losing_other_originals(self):
        doc=document(); second=evidence(1,[.1,.3,.8,.35],'材質： SUS304','PDF文字層')
        second['id']='ev_second'; second['ocr_confidence']=1; doc['regions'].append(second)
        selected=requirement('material','S45C',evidence_ids=['ev_material'])
        other=requirement('material','SUS304',evidence_ids=['ev_second'])
        doc['requirements']=[selected,other]
        row=self.row(selected,status='衝突／待確認',candidate_ids=[selected['id'],other['id']],
                     proposal_issues=['multiple_conflicting_values'],
                     proposal_alternatives=[{'requirement_id':other['id'],'value':'FABRICATED',
                                             'source':{'region_id':'invented'},'score':79,'reason':'second_actual_source'}])
        field=self.build(doc,row)['fields']['material']
        self.assertEqual(field['raw'],'S45C'); self.assertEqual(field['status'],'conflict')
        self.assertFalse(field['human_reviewed']); self.assertEqual(field['alternatives'][0]['raw'],'SUS304')
        self.assertEqual(field['alternatives'][0]['source']['text'],'材質： SUS304')
        self.assertEqual(field['alternatives'][0]['selected_requirement_id'],other['id'])
        self.assertEqual(field['alternatives'][0]['proposal_rank']['score'],79)
        self.assertNotIn('FABRICATED',json.dumps(field)); self.assertNotIn('invented',json.dumps(field))

    def test_proposal_preserves_original_review_without_certifying_new_problem(self):
        doc=document(); selected=requirement('material','S45C',evidence_ids=['ev_material'],
                                             status='verified',support_verified=True)
        doc['requirements']=[selected]
        field=self.build(doc,self.row(selected,proposal_issues=['role_requires_confirmation']))['fields']['material']
        self.assertTrue(field['original_human_reviewed']); self.assertTrue(field['support_verified'])
        self.assertEqual(field['review_status'],'verified'); self.assertFalse(field['human_reviewed'])
        self.assertEqual(field['status'],'needs_review'); self.assertEqual(field['confidence'],.97)

    def test_clean_existing_verified_source_remains_an_original_review_fact(self):
        doc=document(); selected=requirement('material','S45C',evidence_ids=['ev_material'],
                                             status='verified',support_verified=True)
        doc['requirements']=[selected]
        field=self.build(doc,self.row(selected))['fields']['material']
        self.assertTrue(field['human_reviewed']); self.assertTrue(field['original_human_reviewed'])
        self.assertEqual(field['status'],'needs_review'); self.assertTrue(field['proposed'])

    def test_placeholder_without_actual_value_exports_null(self):
        doc=document(); row=self.row(None,proposed=False,display_value='請補填材質',status='待補資料')
        field=self.build(doc,row,report())['fields']['material']
        self.assertIsNone(field['raw']); self.assertIsNone(field['normalized'])
        self.assertEqual(field['status'],'missing'); self.assertFalse(field['proposed'])
        self.assertIsNone(field['selected_proposal']); self.assertNotIn('請補填材質',json.dumps(field))

    def test_nonfinite_or_boolean_proposal_scores_are_not_exported_as_confidence(self):
        doc=document(); selected=requirement('material','S45C',evidence_ids=['ev_material'])
        doc['requirements']=[selected]
        field=self.build(doc,self.row(selected,proposal_score=float('nan'),proposal_confidence=True))['fields']['material']
        self.assertIsNone(field['selected_proposal']['score']); self.assertIsNone(field['selected_proposal']['confidence'])
        self.assertEqual(field['confidence'],.97); self.assertEqual(field['status'],'needs_review')
        json.dumps(field,allow_nan=False)

    def test_competing_values_issue_remains_conflict_even_if_row_label_is_pending(self):
        doc=document(); selected=requirement('material','S45C',evidence_ids=['ev_material'])
        doc['requirements']=[selected]
        field=self.build(doc,self.row(selected,proposal_issues=['competing_values']))['fields']['material']
        self.assertEqual(field['status'],'conflict'); self.assertFalse(field['human_reviewed'])
        self.assertEqual(field['proposal_issues'],['competing_values'])

    def test_placeholder_does_not_revive_explicitly_cleared_source(self):
        doc=document(); selected=requirement('material','S45C',evidence_ids=['ev_material'])
        doc['requirements']=[selected]; doc['quotation_mapping']={'material':None}
        row=self.row(None,proposed=False,candidate_ids=[selected['id']],display_value='已取消帶入／請選候選')
        field=self.build(doc,row)['fields']['material']
        self.assertIsNone(field['raw']); self.assertEqual(field['status'],'missing')
        self.assertIn('manual_source_cleared',field['issues']); self.assertEqual(field['alternatives'][0]['raw'],'S45C')
        self.assertFalse(field['proposed']); self.assertIsNone(field['selected_proposal'])


class SkillExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='drawiq-skill-export-')
        self.root=Path(self.temp.name)
        self.patch=patch('workbench.skill_records._skill_report',side_effect=lambda doc:report(skill_field()))
        self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()

    def contents(self,root):
        return {path.relative_to(root).as_posix():path.read_bytes() for path in root.rglob('*') if path.is_file()}

    def test_batch_order_duplicate_and_repeat_are_byte_identical(self):
        a=document('A'); b=document('B')
        first=self.root/'first'; second=self.root/'second'
        summary=export_records([b,a,a],first)
        export_records([a,b],second)
        self.assertEqual(summary['document_count'],2); self.assertEqual(self.contents(first),self.contents(second))
        before=self.contents(first); export_records([a,b],first); self.assertEqual(before,self.contents(first))
        self.assertIsNone(summary['field_precision']); self.assertEqual(summary['ground_truth_status'],'not_provided')
        for name,expected in summary['files'].items():
            self.assertEqual(hashlib.sha256((first/name).read_bytes()).hexdigest(),expected)

    def test_document_ids_cannot_escape_or_collide_by_filename(self):
        out=self.root/'batch'; docs=[document('../outside'),document('CON'),document('con'),document('A/B'),document('A\\B')]
        export_records(docs,out)
        self.assertFalse((self.root/'outside.json').exists())
        files=list((out/'records').glob('*.json')); self.assertEqual(len(files),5)
        self.assertTrue(all(len(file.stem)==64 for file in files))
        self.assertEqual({json.loads(file.read_text(encoding='utf-8'))['document_id'] for file in files},{doc['id'] for doc in docs})

    def test_preexisting_record_of_other_document_is_not_overwritten(self):
        out=self.root/'batch'; (out/'records').mkdir(parents=True)
        target=out/'records'/(hashlib.sha256(b'new').hexdigest()+'.json')
        target.write_text(json.dumps({'schema_version':'drawiq-skill-record/0.9.0','document_id':'unrelated'}),encoding='utf-8')
        before=target.read_bytes()
        with self.assertRaises(ValueError): export_records(document('new'),out)
        self.assertEqual(target.read_bytes(),before)

    def test_no_pairing_by_same_filename_or_similar_spec(self):
        first=document('quote'); second=document('drawing')
        first['name']=second['name']='Drawing_001.pdf'
        export_records([first,second],self.root/'batch')
        records=[json.loads(line) for line in (self.root/'batch'/'results.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(record['relations']==[] and not record['relation_policy']['inferred_pairing'] for record in records))

    def test_owned_0_9_0_batch_can_upgrade_same_document_and_remove_stale_owned_record(self):
        out=self.root/'batch'; export_records([document('keep'),document('stale')],out)
        old_summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
        old_summary['schema_version']='drawiq-skill-record/0.9.0'
        (out/'summary.json').write_text(json.dumps(old_summary),encoding='utf-8')
        stale=out/'records'/(hashlib.sha256(b'stale').hexdigest()+'.json')
        keep=out/'records'/(hashlib.sha256(b'keep').hexdigest()+'.json')
        old_record=json.loads(keep.read_text(encoding='utf-8')); old_record['schema_version']='drawiq-skill-record/0.9.0'
        keep.write_text(json.dumps(old_record),encoding='utf-8')
        export_records(document('keep'),out)
        self.assertFalse(stale.exists())
        self.assertEqual(json.loads(keep.read_text(encoding='utf-8'))['schema_version'],SCHEMA_VERSION)

    def test_review_queue_includes_pending_proposal_score_and_original_review(self):
        doc=document(); selected=requirement('material','S45C',evidence_ids=['ev_material'])
        doc['requirements']=[selected]
        row=ProposalRecordTests().row(selected,proposal_issues=['role_requires_confirmation'])
        with patch('workbench.skill_records.quotation_rows',return_value=[row]):
            export_records(doc,self.root/'batch')
        queue=[json.loads(line) for line in (self.root/'batch'/'review_queue.jsonl').read_text(encoding='utf-8').splitlines()]
        field=next(item for item in queue if item['field']=='material')
        self.assertTrue(field['proposed']); self.assertFalse(field['human_reviewed'])
        self.assertEqual(field['selected_proposal']['score'],87.3)
        self.assertIn('role_requires_confirmation',field['reasons']); self.assertIn('ev_material',field['region_ids'])

    def test_conflicting_versions_fail_before_writing_anything(self):
        first=document(); second=copy.deepcopy(first); second['version']=4
        out=self.root/'batch'
        with self.assertRaises(ValueError): export_records([first,second],out)
        self.assertFalse(out.exists())

    def test_independent_batch_removes_only_previously_owned_records(self):
        out=self.root/'batch'; export_records([document('old')],out)
        oldfile=next((out/'records').glob('*.json'))
        (out/'keep.txt').write_text('user file',encoding='utf-8')
        (out/'records'/'unowned.json').write_text('{}',encoding='utf-8')
        export_records(document('new'),out)
        self.assertFalse(oldfile.exists()); self.assertEqual((out/'keep.txt').read_text(),'user file')
        self.assertTrue((out/'records'/'unowned.json').exists())
        self.assertEqual(len((out/'results.jsonl').read_text(encoding='utf-8').splitlines()),1)

    def test_replace_failure_rolls_back_entire_previous_batch(self):
        out=self.root/'batch'; export_records(document('old'),out); before=self.contents(out)
        replace=os.replace; calls=0
        def fail_once(source,target):
            nonlocal calls
            calls+=1
            if calls==3: raise OSError('controlled replace failure')
            return replace(source,target)
        with patch('workbench.skill_records.os.replace',side_effect=fail_once):
            with self.assertRaises(OSError): export_records(document('new'),out)
        self.assertEqual(self.contents(out),before)

    def test_review_queue_includes_sources_and_unknown_type(self):
        doc=document(); doc['regions'][0]['ocr_confidence']=.3
        with patch('workbench.skill_records._skill_report',return_value=report(skill_field(),dtype='unknown')):
            export_records(doc,self.root/'batch')
        queue=[json.loads(line) for line in (self.root/'batch'/'review_queue.jsonl').read_text(encoding='utf-8').splitlines()]
        material=next(item for item in queue if item['field']=='material')
        self.assertIn('ev_material',material['region_ids']); self.assertIn('low_engine_confidence',material['reasons'])
        self.assertTrue(any(item['field']=='document_type' and 'unknown_document' in item['reasons'] for item in queue))


class SkillRecordIntegrationTests(unittest.TestCase):
    def test_actual_rules_keep_raw_spacing_and_blank_price_review(self):
        payload={'document_id':'actual_rules','document_type':'quote_label',
                 'pages':[{'page':1,'width':1000,'height':1000,'lines':[
                     {'text':'報價單號： DQ26004001','bbox':[10,10,500,35],'confidence':.98},
                     {'text':'規格： M8 × 1.25 - 6h','bbox':[10,50,500,75],'confidence':.95},
                     {'text':'數量： 10 M','bbox':[10,90,500,115],'confidence':.94},
                     {'text':'單價： / TWD / M','bbox':[10,130,500,155],'confidence':.97}]}]}
        doc=from_ocr_json(payload); before=copy.deepcopy(doc)
        record=build_record(doc); spec=record['fields']['product.specification']
        self.assertEqual(spec['original_raw'],' M8 × 1.25 - 6h')
        self.assertEqual(spec['normalized']['tolerance'],'6h'); self.assertIsNone(spec['normalized']['length_value'])
        self.assertIn('quantity_unit_ambiguous',record['fields']['quotation.quantity']['issues'])
        self.assertIsNone(record['fields']['quotation.price']['normalized'])
        self.assertEqual(record['fields']['quotation.price']['original_raw'],' / TWD / M')
        self.assertEqual(spec['source']['confidence_kind'],'supplied_transcription_score')
        self.assertEqual(doc,before)

    def test_actual_rule_conflict_retains_both_rows_and_missing_bbox(self):
        doc=from_ocr_json({'document_id':'actual_conflict','document_type':'quote_label',
                          'pages':[{'page':1,'lines':[{'text':'材質： S45C','confidence':.99},
                                                     {'text':'材質： SUS304','confidence':.9}]}]})
        record=build_record(doc); field=record['fields']['material']
        self.assertEqual(field['status'],'conflict'); self.assertIsNone(field['raw'])
        self.assertEqual(len(field['alternatives']),2)
        self.assertTrue(all(source['bbox'] is None for source in field['sources']))
        self.assertTrue(all(source['text'].startswith('材質：') for source in field['sources']))


if __name__=='__main__': unittest.main()
