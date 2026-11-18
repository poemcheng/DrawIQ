import os
import unittest
from unittest.mock import patch
from workbench.domain import evidence, clone, validate_document
from workbench.quotation import extract_quotation, is_quotation
from workbench.quotation_ocr import combine_candidates
from workbench.knowledge import summary, build_graph, summary_html
from workbench.extraction import analyze, apply_analysis, map_box
from tests.test_workbench import Fixture

def lines(texts):
    return [evidence(1,[.02,.02+i*.05,.94,.055+i*.05],t,'synthetic') for i,t in enumerate(texts)]

def quote_doc(doc,number='Q-SYNTHETIC-01',customer='測試甲公司',material='測試鋼材'):
    d=clone(doc); d['regions']=lines(['報價單號：'+number,'客戶：'+customer,'材質：'+material,'規格：M6x1x30','報價膠別：測試膠 A','數量：200 M','報價：/ TWD / M','報價日期：2026/4/16','報價有效日期：2026/5/16'])
    d['requirements']=extract_quotation(d['regions']); return d

class QuotationParserTests(unittest.TestCase):
    def parse(self,*texts): return {r['key']:r for r in extract_quotation(lines(['報價單號：Q-123']+list(texts)))}
    def test_dates_are_distinct_and_calendar_validated(self):
        r=self.parse('報價日期：2026/4/16','報價有效日期：2026/5/16')
        self.assertEqual(r['quotation.date']['value'],'2026-04-16'); self.assertEqual(r['quotation.valid_until']['value'],'2026-05-16')
        self.assertEqual(self.parse('報價日期：2026/2/30')['quotation.date']['state'],'unreadable')
    def test_bare_heading_does_not_create_a_second_price(self):
        r=extract_quotation(lines(['報價','報價單號：Q-123','報價：/ TWD / M']))
        prices=[x for x in r if x['key']=='quotation.price']
        self.assertEqual(len(prices),1); self.assertEqual(prices[0]['unit'],'TWD/M')
    def test_price_currency_layout_recovers_corrupt_label_without_amount(self):
        r=self.parse('搬價：／ TWD／M')
        self.assertEqual(r['quotation.price']['value'],''); self.assertEqual(r['quotation.price']['unit'],'TWD/M')
    def test_uncertain_name_is_preserved_as_two_candidates(self):
        a=extract_quotation(lines(['報價單號：Q-123','報價人員：王測甲']))
        b=extract_quotation(lines(['報價單號：Q-123','搬價人翼王測乙']))
        r=next(r for r in combine_candidates(a,b) if r['key']=='quotation.salesperson')
        self.assertEqual(r['state'],'conflict'); self.assertEqual(len(r['candidates']),2)
    def test_obscured_customer_and_blank_price_never_invented(self):
        r=self.parse('客戶：：','報價：/ TWD / M')
        self.assertEqual((r['customer.name']['value'],r['customer.name']['state']),('','unreadable'))
        self.assertEqual((r['quotation.price']['value'],r['quotation.price']['state']),('','blank'))
        self.assertEqual(r['quotation.currency']['value'],'TWD'); self.assertEqual(r['quotation.price_unit']['value'],'M')
    def test_quantity_and_m_do_not_convert(self):
        r=self.parse('數量：160 M')['quotation.quantity']
        self.assertEqual((r['value'],r['unit']),('160','M')); self.assertTrue(r['extraction_notes'])
    def test_price_amount_and_denominator_are_separate(self):
        r=self.parse('報價：12.50 / USD / PCS')
        self.assertEqual((r['quotation.price']['value'],r['quotation.price']['unit']),('12.50','USD/PCS'))
    def test_missing_blank_and_unreadable_differ(self):
        r=self.parse('專案代號：','客戶：■■■■')
        self.assertEqual(r['material']['state'],'missing'); self.assertEqual(r['quotation.project']['state'],'blank'); self.assertEqual(r['customer.name']['state'],'unreadable')
    def test_spaces_and_simplified_labels_keep_value_orthography(self):
        r=self.parse('客 户：测试甲公司','材 质：娥材','规 格：M8x1.25x50','数 量：1,200 PCS')
        self.assertEqual(r['customer.name']['value'],'测试甲公司'); self.assertEqual(r['material']['value'],'娥材')
        self.assertEqual(r['product.specification']['value'],'M8x1.25x50'); self.assertEqual(r['quotation.quantity']['value'],'1200')
    def test_two_fields_on_row_are_bounded(self):
        r=self.parse('客戶：甲公司  材質：SUS304')
        self.assertEqual(r['customer.name']['value'],'甲公司'); self.assertEqual(r['material']['value'],'SUS304')
    def test_split_boxes_share_a_row_and_all_evidence(self):
        regions=lines(['報價單號：Q-123']); a=evidence(1,[.02,.15,.2,.19],'數量：'); b=evidence(1,[.25,.15,.5,.19],'90 PCS'); regions += [a,b]
        r=next(r for r in extract_quotation(regions) if r['key']=='quotation.quantity')
        self.assertEqual(r['value'],'90'); self.assertEqual(set(r['evidence_ids']),{a['id'],b['id']})
    def test_multiline_notes_end_at_next_field(self):
        r=self.parse('包裝方式：桶進桶出','2. 至少塗4牙以上','注意事項：需打樣','4. 先試扭力','聯絡電話：555-0100')
        self.assertIn('至少塗4牙',r['packaging.instructions']['value']); self.assertNotIn('聯絡',r['quotation.notes']['value'])
        self.assertEqual(r['contact.phone']['value'],'555-0100')
    def test_generic_engineering_sheet_not_quotation(self):
        regions=lines(['材料：SUS304','數量：100 PCS','比例：1:2'])
        self.assertFalse(is_quotation(regions)); self.assertEqual(extract_quotation(regions),[])
    def test_partial_crop_does_not_create_missing_whole_document(self):
        r=extract_quotation(lines(['材質：SUS304']),detected=True,include_missing=False)
        self.assertEqual([x['key'] for x in r],['material'])
    def test_row_pass_preserves_conflict_and_recovers_unit(self):
        a=extract_quotation(lines(['報價單號：Q-123','數量：90','材質：SUS301','報價：/ TWD']))
        b=extract_quotation(lines(['報價單號：Q-123','數量：90 M','材質：SUS304','報價：/ TWD / M']))
        r={r['key']:r for r in combine_candidates(a,b)}
        self.assertEqual(r['quotation.quantity']['unit'],'M'); self.assertEqual(r['quotation.price_unit']['value'],'M')
        self.assertEqual(r['material']['state'],'conflict'); self.assertGreaterEqual(len(r['material']['candidates']),2)

class QuotationIntegrationTests(Fixture):
    def test_old_ocr_gets_readonly_field_table_with_stable_fact_ids(self):
        d=quote_doc(self.doc); d['requirements']=[]; before=clone(d)
        a=summary(d); b=summary(d)
        self.assertEqual(a,b); self.assertEqual(d,before)
        self.assertTrue(a['quotation_fields']); self.assertIn('欄位擷取',summary_html(d))
        self.assertTrue(all(f['requirement_id'] is None for f in a['quotation_fields']))
    def test_analyze_quotation_skips_numeric_noise_and_retains_coordinates(self):
        from drawing_intelligence.models import TextRegion
        from drawing_intelligence.ocr import OCRResult
        regions=lines(['報價單號：Q-123','客戶：甲公司','材質：SUS304','數量：90 M'])
        w,h=400,600; crop=[.2,.1,.8,.9]
        # analyzed crop image size differs; boxes are explicitly normalized against it.
        def fake(path,**kwargs):
            from PIL import Image
            with Image.open(path) as im: width,height=im.size
            return OCRResult('\n'.join(e['text'] for e in regions),[TextRegion(e['text'],tuple(round(x*(width if i%2==0 else height)) for i,x in enumerate(e['box'])),.9,'PDF文字層') for e in regions],'PDF文字層')
        with patch('drawing_intelligence.ocr.recognize_page',side_effect=fake): result=analyze(self.doc,self.store.root,crop=crop,rotation=90)
        self.assertFalse(any(r['key'].startswith('dimension.') for r in result['requirements']))
        self.assertEqual(next(r for r in result['requirements'] if r['key']=='quotation.quantity')['unit'],'M')
        for a,b in zip(result['regions'][0]['box'],map_box(regions[0]['box'],90,crop)): self.assertAlmostEqual(a,b,places=2)
        updated=apply_analysis(self.store,self.doc['id'],self.doc['version'],result); validate_document(updated)
    def test_verified_quote_field_not_overwritten_by_reanalysis(self):
        d=quote_doc(self.doc); d=self.store.mutate(d['id'],1,'test','synthetic fixture',lambda x:x.update(regions=d['regions'],requirements=d['requirements']))
        material=next(r for r in d['requirements'] if r['key']=='material'); d=self.store.verify_requirement(d['id'],d['version'],material['id'],'synthetic verification')
        output=clone(d); output['requirements']=[{**clone(material),'value':'WRONG'}]; output['tool_runs']=[]
        new=apply_analysis(self.store,d['id'],d['version'],output)
        r=next(r for r in new['requirements'] if r['id']==material['id']); self.assertEqual(r['value'],'測試鋼材'); self.assertEqual(r['status'],'needs_review')
    def test_mixed_page_keeps_geometry_outside_quotation_fields(self):
        from drawing_intelligence.models import TextRegion
        from drawing_intelligence.ocr import OCRResult
        texts=['報價單號：Q-123','客戶：甲公司','材質：SUS304','數量：90 PCS','10 mm']
        def fake(path,**kwargs):
            from PIL import Image
            with Image.open(path) as im: width,height=im.size
            boxes=[TextRegion(t,(10,30+i*35,width-10,56+i*35),.95,'PDF文字層') for i,t in enumerate(texts)]
            return OCRResult('\n'.join(texts),boxes,'PDF文字層')
        with patch('drawing_intelligence.ocr.recognize_page',side_effect=fake): result=analyze(self.doc,self.store.root)
        dims=[r for r in result['requirements'] if r['key'].startswith('dimension.')]
        self.assertTrue(any(r['value']=='10 mm' for r in dims))
        self.assertFalse(any('90' in r['value'] for r in dims))

class EntityNetworkTests(Fixture):
    def test_shared_entities_join_two_quotes_without_category_tree(self):
        a=quote_doc(self.doc); b=quote_doc(self.doc,'Q-SYNTHETIC-02'); b['id']='second-document'
        g=build_graph([a,b]); material=[n for n in g['nodes'] if n['type']=='Material']
        self.assertEqual(len(material),1)
        incoming=[e for e in g['edges'] if e['relation']=='specifies_material' and e['target']==material[0]['id']]
        self.assertEqual(len(incoming),2); self.assertTrue(all(e['provenance'] for e in incoming))
        self.assertFalse(any(n['type'] in ('Value','Category') for n in g['nodes']))
        ids={n['id'] for n in g['nodes']}; self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in g['edges']))
    def test_type_and_tenant_boundaries_prevent_bad_merge(self):
        a=quote_doc(self.doc,customer='SAME',material='SAME'); b=clone(a); b['id']='b'; b['tenant']='another-tenant'
        g=build_graph([a,b]); selected=[n for n in g['nodes'] if n['type'] in ('Customer','Material')]
        self.assertEqual(len({n['id'] for n in selected}),4)
    def test_unknowns_are_not_entities_or_zero_prices(self):
        g=build_graph([quote_doc(self.doc,customer='■■■■')])
        self.assertFalse(any(n['type']=='Customer' for n in g['nodes'])); self.assertTrue(g['unresolved'])
        attrs=[a for n in g['nodes'] for a in n.get('attributes',[])]
        self.assertFalse(any(a['key']=='quotation.price' for a in attrs))
    def test_approved_only_does_not_leak_unreviewed_evidence(self):
        d=self.approved(); hidden=evidence(1,[.1,.5,.9,.6],'UNREVIEWED SECRET TEST'); d['regions'].append(hidden)
        g=build_graph([d],approved_only=True)
        self.assertFalse(any(n.get('region_id')==hidden['id'] for n in g['nodes']))
        self.assertTrue(any(e['relation']=='supported_by' for e in g['edges']))
        self.assertEqual(build_graph([quote_doc(self.doc)],approved_only=True)['nodes'],[])
    def test_conflicting_customer_not_shared_as_fact(self):
        d=quote_doc(self.doc); next(r for r in d['requirements'] if r['key']=='customer.name')['state']='conflict'
        self.assertFalse(any(n['type']=='Customer' for n in build_graph([d])['nodes']))
    def test_graph_geometry_reacts_to_drag(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QPointF
        from workbench.graph_view import EntityNode,RelationEdge,network_positions
        app=QApplication.instance() or QApplication([])
        g=build_graph([quote_doc(self.doc)]); nodes=[n for n in g['nodes'] if n['layer']=='entity']; edges=[e for e in g['edges'] if e['layer']=='semantic']
        positions=network_positions(nodes,edges); self.assertEqual(len(positions),len(nodes))
        e=edges[0]; byid={n['id']:n for n in nodes}; a=EntityNode(byid[e['source']],positions[e['source']],lambda _:None); b=EntityNode(byid[e['target']],positions[e['target']],lambda _:None)
        edge=RelationEdge(e,a,b,lambda _:None); before=edge.path(); a.setPos(a.pos()+QPointF(45,60)); self.assertNotEqual(edge.path(),before)
