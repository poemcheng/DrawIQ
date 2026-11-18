from pathlib import Path
import argparse
import json
import os
import sys

def launch_gui(data_root):
    from workbench import __version__
    from workbench.startup import desktop_environment,stage,failure
    try:
        desktop_environment()
        stage('application.start',version=__version__,python=sys.executable,data_root=str(data_root.resolve()))
        stage('ui.import.start')
        from workbench.ui import main as gui
        stage('ui.import.ready')
        return gui(data_root)
    except Exception as exc:
        failure('application.failed')
        if sys.platform=='win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                'DrawIQ 無法啟動：'+str(exc)+'\n\n請查看工作台資料夾的 launcher.log。',
                'DrawIQ 啟動錯誤',16)
        return 1

def main():
    parser=argparse.ArgumentParser(description='DrawIQ engineering evidence workbench')
    parser.add_argument('--data',type=Path,default=Path(__file__).resolve().parent/'data')
    parser.add_argument('--user',help='互動式 CLI 登入帳號（密碼不經命令列傳入）')
    sub=parser.add_subparsers(dest='command')
    p=sub.add_parser('import'); p.add_argument('files',type=Path,nargs='+')
    p=sub.add_parser('legacy'); p.add_argument('json_file',type=Path)
    sub.add_parser('example')
    p=sub.add_parser('analyze'); p.add_argument('document_id'); p.add_argument('--vlm',action='store_true'); p.add_argument('--model',default='qwen3-vl:8b')
    sub.add_parser('list')
    p=sub.add_parser('summary'); p.add_argument('document_id')
    p=sub.add_parser('corpus'); p.add_argument('--include-examples',action='store_true')
    sub.add_parser('index')
    p=sub.add_parser('semantic-search'); p.add_argument('query'); p.add_argument('--approved-only',action='store_true')
    p=sub.add_parser('business-export'); p.add_argument('document_id')
    p=sub.add_parser('export'); p.add_argument('document_id'); p.add_argument('--release-id')
    p=sub.add_parser('serve'); p.add_argument('--port',type=int,default=8769)
    args=parser.parse_args()
    os.environ['DRAWIQ_DATA_ROOT']=str(args.data.resolve())
    if not args.command:
        return launch_gui(args.data)
    from workbench.store import Store
    from workbench.ingest import prepare_document,import_legacy,import_example,REPO
    from workbench.auth import authenticate_cli
    auth,session=authenticate_cli(args.data,args.user)
    store=Store(args.data,actor=lambda:auth.actor(session))
    if args.command=='import': result=[store.add(prepare_document(f,store.root))['id'] for f in args.files]
    elif args.command=='legacy': result=import_legacy(store,args.json_file,print)
    elif args.command=='example': result=import_example(store,REPO/'analysis/drawing_006_knowledge_example.json')['id']
    elif args.command=='list': result=[{'id':d['id'],'name':d['name'],'status':d['status'],'requirements':sum(not r['deleted'] for r in d['requirements'])} for d in store.list_documents()]
    elif args.command=='summary':
        from workbench.knowledge import summary
        result=summary(store.get(args.document_id))
    elif args.command=='corpus':
        from workbench.corpus import export_corpus
        result=export_corpus(store,include_examples=args.include_examples,progress=print)
    elif args.command=='index':
        from workbench.retrieval import build_index
        result=build_index(store,progress=print)
    elif args.command=='semantic-search':
        from workbench.retrieval import search
        result=search(store,args.query,approved_only=args.approved_only)
    elif args.command=='business-export':
        from workbench.business import BusinessService
        result=str(BusinessService(store).export(args.document_id))
    elif args.command=='analyze':
        from workbench.extraction import apply_analysis
        from workbench.bounded import analyze
        d=store.get(args.document_id); output=analyze(d,store.root,use_vlm=args.vlm,model=args.model,progress=print)
        updated=apply_analysis(store,d['id'],d['version'],output); result={'id':updated['id'],'requirements':len(updated['requirements']),'warnings':updated['warnings']}
    elif args.command=='export':
        from workbench.exporting import export_bundle
        result=str(export_bundle(store,args.document_id,release_id=args.release_id))
    elif args.command=='serve':
        auth.require(session,admin=True)
        import uvicorn
        from workbench.api import create_app
        token=os.environ.get('DRAWIQ_API_TOKEN','')
        if not token: raise ValueError('請設定 DRAWIQ_API_TOKEN（至少 24 字元）；API 不會自動建立或顯示金鑰')
        uvicorn.run(create_app(store.root,token),host='127.0.0.1',port=args.port); return 0
    print(json.dumps(result,ensure_ascii=False,indent=2)); auth.logout(session); return 0

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr,'reconfigure'): sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
