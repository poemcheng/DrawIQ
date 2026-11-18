"""Replay supplied OCR JSON through the production skill; does not run OCR or a model."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.ocr_contract import from_ocr_json
from workbench.engineering import catalogue_candidates
from workbench.skill_records import export_records


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True,help='OCR JSON file or directory of JSON files')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    files=sorted(args.input.glob('*.json')) if args.input.is_dir() else [args.input]
    try:
        if not files: raise ValueError('輸入資料夾沒有 OCR JSON')
        docs=[]
        for path in files:
            body=path.read_bytes(); doc=from_ocr_json(json.loads(body.decode('utf-8-sig')))
            doc['source_copy']=str(path.resolve())
            doc['source_sha256']=hashlib.sha256(body).hexdigest()
            doc['requirements'].extend(catalogue_candidates(doc))
            docs.append(doc)
        if len({d['id'] for d in docs})!=len(docs): raise ValueError('批次 document_id 不可重複')
        summary=export_records(docs,args.output)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'passed':False,'error':str(exc),'ocr_executed':False},ensure_ascii=False),file=sys.stderr)
        return 2
    print(json.dumps({'passed':True,'documents':len(docs),'output':str(args.output.resolve()),
                      'ocr_executed':False,'summary':summary},ensure_ascii=False))
    return 0


if __name__=='__main__': raise SystemExit(main())
