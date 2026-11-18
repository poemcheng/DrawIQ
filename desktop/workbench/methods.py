"""Inspectable model/method inventory; run states come from actual traces."""
from pathlib import Path
from .engineering import VERSION,metadata

METHODS=[
    ('PDF 文字層','PDF 原生字元與位置；掃描 PDF 沒有可用文字層時交由 OCR'),
    ('PP-OCRv6-small','本機 ONNX：文字偵測、方向分類、文字辨識；保留原文與原始分數'),
    ('Indra OCR 1.1.2 / v1.1.0','本機 ONNX 多語言模型；保留原讀值，將模型旋轉後的框映回原圖'),
    ('四向 OCR 比對','比較 0／90／180／270 度縮圖上的工程文字線索；低證據時維持原方向，結果仍須人工覆核'),
    ('型別化工程標註 / 0.10.0','公差上下界、比較方向、螺紋螺距／長度／等級及數量原文；M 不自動換算'),
    ('JSON-LD／SHACL','匯出來源與覆核狀態；僅已確認且有證據的事實進入可信清單，結構通過不代表辨識正確'),
    ('空間欄位／表格解析','標籤對值、同列欄位、格線表格；保留表格列欄與證據'),
    ('工程局部重讀','最多 8 個已測得的工程文字區塊放大重讀；保留原始與新框、單位差異，不猜受損標準號'),
    ('工程上下文解析','螺紋、尺寸、公差、已讀出的符號、塗膠牙數、溫度、年度量與品質文件'),
    ('本機視覺語言模型','僅分析已測得的未解／低信心局部圖框；保存裁切、座標轉換、原始回應與各模型候選；無需重讀時記錄 not_needed'),
    ('耐落標籤對應','16 個客製欄位先帶入最適合候選；低信心、衝突及工程上下文以建議待確認呈現；無實值明確待補填，人工確認後保留來源'),
    ('quote-drawing-extraction Skill','文件分類、標籤鄰接、螺紋規格結構、原始單位、日期順序與缺值／衝突檢查；保存原文及來源契約'),
    ('人工覆核學習','已確認案例的上下文相似模型用於候選排序；既有核准修正策略可另行測試／啟用'),
    ('Harness Engineering','版本化工程規則、來源契約、架構檢查與可重跑的驗證案例；人工回饋可提升為回歸案例'),
    ('ShapeLab 清除／相似模型','獨立零件視圖工具；清除遮罩與零件相似，不作工程文字或報價辨識')]

def report(doc=None):
    root=Path(__file__).resolve().parents[2]
    modelroot=root/'DrawIQ_ShapeLab'/'data'/'models'
    from .harness import inspect_document,policy
    from .quote_skill import analyze
    from .quotation_layout import quotation_rows
    return {'schema':'drawiq-methods/0.9.1','catalogue_version':VERSION,
            'quote_skill':analyze(doc) if doc else None,
            'quotation_prefill':[{k:r[k] for k in ('key','label','display_value','status','automatic','proposed',
                                    'proposal_reason','proposal_issues','proposal_confidence','proposal_alternatives')}
                                 for r in quotation_rows(doc,include_proposals=True)] if doc else [],
            'harness':inspect_document(doc) if doc else {'policy':policy()},
            'methods':[{'name':n,'purpose':p} for n,p in METHODS],
            'shapelab':{'path':str(modelroot),'weights':[p.name for p in modelroot.glob('*latest.json')],
                        'used_for_quotation':False},
            'document_id':doc['id'] if doc else None,'tool_runs':doc.get('tool_runs',[]) if doc else [],
            'coverage':'全部已取得的文字／符號／尺寸候選均保留；執行完成不代表沒有漏字、漏符號或錯讀。',
            'neural_training':'框選軌跡與修正可匯出監督資料；本版不自動重訓 OCR 或 VLM 權重。'}

def candidate_report(doc):
    regions={e['id']:e for e in doc['regions']}
    return [{'requirement_id':r['id'],'key':r['key'],'value':r['value'],'unit':r['unit'],
             'status':r['status'],'origin':r['origin'],'evidence_ids':r['evidence_ids'],**metadata(r,regions)}
            for r in doc['requirements'] if not r['deleted']]
