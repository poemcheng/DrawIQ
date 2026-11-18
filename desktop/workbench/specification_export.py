"""Printable, escaped specification sheet plus business workbook annexes."""
import html,json
from openpyxl.styles import Font,PatternFill,Alignment
from .specification import PROFILES,ITEM_STATES,COMMERCIAL_LABELS,spec_status

def export_spec_files(folder,case,doc,payload,book):
    esc=lambda s:html.escape(str(s if s is not None else ''))
    spec=case['specification']; status=spec_status(case); issues=payload['quote_readiness_issues']
    content=['<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>規格確認單</title><style>body{font:15px/1.65 Microsoft JhengHei,sans-serif;color:#173b44;max-width:1100px;margin:32px auto}table{border-collapse:collapse;width:100%;margin:18px 0}td,th{border:1px solid #b5c9ce;padding:9px;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#edf5f6}h2{border-bottom:2px solid #46858e}.stamp{padding:15px;background:#eef5f6}.warning{color:#8c4720}@media print{body{margin:12mm;font-size:10pt}tr{break-inside:avoid}thead{display:table-header-group}}</style><body><h1>規格確認單</h1>']
    content.append('<p class="stamp">'+esc(status)+'｜'+esc(PROFILES[spec['profile']])+f'｜業務版本 v{case["version"]}</p>')
    content.append('<p>來源：'+esc(doc['name'])+f' v{doc["version"]}｜SHA-256 '+esc(doc['source_sha256'])+'</p>')
    for key,label in [('form_number','確認單號'),('product_name','產品／服務'),('drawing_number','圖號／文件號'),('drawing_revision','文件版次')]: content.append('<p>'+label+'：'+esc(spec[key] or '待填')+'</p>')
    for key,label in [('customer','客戶／供應商'),('project','案件'),('quantity','本次數量'),('unit','單位'),('due_date','要求交期')]:content.append('<p>'+label+'：'+esc(case['rfq'][key] or '待填')+'</p>')
    content.append('<table><thead><tr><th>規格</th><th>判定</th><th>內容／供應範圍</th><th>驗收／判定方式</th><th>不適用原因／依據</th><th>來源需求</th></tr></thead><tbody>')
    for row in spec['items']: content.append('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in [row['label'],ITEM_STATES[row['state']],row['value'],row['acceptance'],row['reason'],', '.join(row['requirement_ids'])])+'</tr>')
    content.append('</tbody></table><p>待確認事項：'+esc(spec['open_issues'] or '未記錄未結案事項')+'</p><h2>確認紀錄</h2>')
    review=spec.get('review'); ack=spec.get('customer_confirmation')
    content.append('<p>內部確認：'+esc((review['actor']+'｜'+review['at']) if review else '尚未完成')+'</p>')
    content.append('<p>客戶确认：'+esc((ack['name']+'｜'+ack['confirmed_on']+'｜'+ack['reference']) if ack else '尚未記錄客戶回覆')+'</p>')
    content.append('<p>客戶確認欄為承辦人登錄已收到的回覆依據，並非電子簽章；請保管原始簽回文件。</p><h2>報價與商務準備檢核</h2>')
    content.append('<p class="warning">'+esc('；'.join(issues) if issues else '資料備齊，可送內部商務審核；本包仍為草稿，未對外發送。')+'</p>')
    content.append('<table><tr><th>商務條件</th><th>內容</th></tr>')
    for key,label in COMMERCIAL_LABELS.items(): content.append('<tr><td>'+esc(label)+'</td><td>'+esc(case['commercial'][key] or '待填')+'</td></tr>')
    content.append('</table>')
    if payload['quote_calculation']:
        calc=payload['quote_calculation']; content.append('<h2>內部報價試算</h2><p>'+esc(calc['currency']+' '+calc['basis_quote']+' / '+calc['price_basis_qty']+' '+calc['unit'])+'</p><p>未稅總額：'+esc(calc['total_quote'])+'｜外加稅額：'+esc(calc['tax_amount'] if calc['tax_amount'] is not None else '待確認')+'｜含稅總額：'+esc(calc['total_with_tax'] if calc['total_with_tax'] is not None else '待確認')+'</p>')
    content.append('<h2>來源需求附錄（含原文與位置）</h2><table><thead><tr><th>需求</th><th>值／條件</th><th>狀態</th><th>原圖證據</th></tr></thead><tbody>')
    regions={e['id']:e for e in doc['regions']}
    for r in doc['requirements']:
        if r['deleted']: continue
        evidence='\n'.join(f'P{regions[x]["page"]} {regions[x]["box"]}：{regions[x]["text"]}' for x in r['evidence_ids'] if x in regions)
        value=f'{r["operator"]} {r["value"]} {r["unit"]}\n對象：{r["subject"]}\n範圍：{r["scope"]}\n條件：{r["conditions"]}\n公差：+{r.get("upper_tolerance","")} / {r.get("lower_tolerance","")}'
        content.append('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in [r['label']+'\n'+r['id'],value,r['status']+' / '+r['state'],evidence])+'</tr>')
    content.append('</tbody></table></body></html>')
    (folder/'規格確認單.html').write_text(''.join(content).replace('客戶确认','客戶確認'),encoding='utf-8')
    (folder/'specification.json').write_text(json.dumps({'document_id':doc['id'],'source_version':doc['version'],'source_sha256':doc['source_sha256'],'business_version':case['version'],'status':status,'rfq':case['rfq'],'specification':spec,'commercial':case['commercial'],'quote_readiness_issues':issues},ensure_ascii=False,indent=2),encoding='utf-8')
    w=book.create_sheet('規格確認單'); w.append(['確認單號',spec['form_number'],'狀態',status]); w.append(['來源文件',doc['name'],'來源版本',str(doc['version'])]); w.append(['產品／服務',spec['product_name'],'文件版次',spec['drawing_revision']])
    w.append(['規格','判定','內容／供應範圍','驗收／判定方式','不適用原因／依據','來源需求'])
    for row in spec['items']: w.append([row['label'],ITEM_STATES[row['state']],row['value'],row['acceptance'],row['reason'],','.join(row['requirement_ids'])])
    w.append(['待確認事項',spec['open_issues']]); w.append(['內部確認',json.dumps(review,ensure_ascii=False) if review else '尚未完成']); w.append(['客戶確認紀錄',json.dumps(ack,ensure_ascii=False) if ack else '尚未收到'])
    w=book.create_sheet('商務條件與檢核'); w.append(['項目','內容'])
    for key,label in COMMERCIAL_LABELS.items(): w.append([label,case['commercial'][key]])
    for issue in issues: w.append(['待處理',issue])
    if not issues: w.append(['狀態','資料備齊，可送內部商務審核；輸出仍為草稿'])
    for w in book:
        for row in w:
            for cell in row:
                if isinstance(cell.value,str): cell.data_type='s'
                cell.alignment=Alignment(vertical='top',wrap_text=True)
        w.freeze_panes='A2'; w.auto_filter.ref=w.dimensions
        for cell in w[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='216570')
        for column in w.columns: w.column_dimensions[column[0].column_letter].width=30
    book.save(folder/'business_workbook.xlsx')
