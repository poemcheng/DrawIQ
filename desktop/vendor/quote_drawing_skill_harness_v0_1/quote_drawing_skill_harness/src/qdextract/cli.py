import argparse,json,sys
from pathlib import Path
from .core import extract


def load_ocr(path,lang):
    if path.suffix.lower()=='.json':return json.loads(path.read_text(encoding='utf8')),'provided_ocr'
    try:
        import fitz,pytesseract
        from PIL import Image
    except ImportError as e:raise RuntimeError('Install requirements.txt') from e
    pages=[]
    if path.suffix.lower()=='.pdf':
        pdf=fitz.open(path)
        for i,p in enumerate(pdf):
            # use native text only if meaningful; drawings may have no text layer
            pix=p.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
            im=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
            pages.append((i+1,im))
    else:pages=[(1,Image.open(path).convert('RGB'))]
    data=[]
    for i,im in pages:
        d=pytesseract.image_to_data(im,lang=lang,config='--psm 11',output_type=pytesseract.Output.DICT)
        grouped={}
        for k,t in enumerate(d['text']):
            if not t.strip():continue
            key=(d['block_num'][k],d['par_num'][k],d['line_num'][k]); grouped.setdefault(key,[]).append(k)
        lines=[]
        for ids in grouped.values():
            left=min(d['left'][k] for k in ids);top=min(d['top'][k] for k in ids)
            right=max(d['left'][k]+d['width'][k] for k in ids);bottom=max(d['top'][k]+d['height'][k] for k in ids)
            cs=[max(0,float(d['conf'][k]))/100 for k in ids if str(d['conf'][k])!='-1']
            lines.append({'text':' '.join(d['text'][k] for k in ids),'bbox':[left,top,right,bottom],'confidence':sum(cs)/len(cs) if cs else 0})
        lines.sort(key=lambda a:(a['bbox'][1],a['bbox'][0]))
        data.append({'page':i,'width':im.width,'height':im.height,'lines':lines})
    return {'document_id':path.stem,'pages':data},'tesseract'


def main():
    ap=argparse.ArgumentParser(description='Evidence-based quotation/drawing rule harness')
    ap.add_argument('--input',required=True);ap.add_argument('--output',default='outputs');ap.add_argument('--lang',default='chi_tra+eng')
    args=ap.parse_args();src=Path(args.input);out=Path(args.output);out.mkdir(parents=True,exist_ok=True);(out/'records').mkdir(exist_ok=True)
    paths=[src] if src.is_file() else sorted(p for p in src.iterdir() if p.suffix.lower() in {'.json','.png','.jpg','.jpeg','.tif','.tiff','.pdf'})
    results=[];reviews=[];errors=[]
    for path in paths:
        try:
            doc,engine=load_ocr(path,args.lang);result=extract(doc,engine)
            results.append(result)
            (out/'records'/f'{path.stem}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
            for field,entry in result['fields'].items():
                if isinstance(entry,dict) and entry.get('status') in {'missing','needs_review','conflict'}:
                    reviews.append({'document_id':result['document_id'],'field':field,'entry':entry})
            for issue in result['issues']:reviews.append({'document_id':result['document_id'],**issue})
            print('OK',path.name,result['document_type'])
        except Exception as e:
            errors.append({'file':str(path),'error':str(e)})
            print('ERROR',path.name,e,file=sys.stderr)
    for name,items in [('results.jsonl',results),('review_queue.jsonl',reviews)]:
        (out/name).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in items),encoding='utf8')
    (out/'summary.json').write_text(json.dumps({'processed':len(results),'review_items':len(reviews),'errors':errors},ensure_ascii=False,indent=2),encoding='utf8')
    if errors:sys.exit(2)
if __name__=='__main__':main()
