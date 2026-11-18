"""Desktop/CLI page isolation; a native OCR or HTTP hang cannot hold the UI worker."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from .domain import clone, uid, now

ANALYSIS_COLLECTIONS = ('requirements','regions','tool_runs','warnings','layout_layers')

def page_budgets(backend):
    from .mineru_runtime import ADVANCED_BACKEND, BACKENDS
    if backend==ADVANCED_BACKEND:
        return 180,180
    if backend in BACKENDS:
        return 90,120
    return 30,30

def atomic_json(path, value):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    os.replace(temp,path)

def supervise(command, *, cwd, deadline, cancelled=lambda:False, log_path, env=None):
    """Return only after the direct child is reaped. Worker must not spawn engines."""
    with log_path.open('w',encoding='utf-8') as log:
        child=subprocess.Popen(command,cwd=cwd,stdout=log,stderr=log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
            env={**os.environ,'PYTHONIOENCODING':'utf-8'} if env is None else env)
        reason='complete'
        try:
            while child.poll() is None:
                if cancelled(): reason='cancelled'; break
                if time.monotonic()>=deadline: reason='partial_timeout'; break
                time.sleep(.025)
        finally:
            if child.poll() is None:
                # Windows venv launchers can own a second interpreter process.
                # Terminate this worker's tree so cancellation cannot leave OCR running.
                if os.name=='nt':
                    try:
                        subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    except (OSError,subprocess.TimeoutExpired): pass
                if child.poll() is None: child.kill()
            child.wait(timeout=1)
        if reason=='complete' and child.returncode: reason='failed'
        return reason

def _analyze_single(doc,data_root,*,page_numbers=None,crop=None,rotation=0,use_vlm=False,
            model='qwen2.5vl:7b',host='http://localhost:11434',progress=lambda _:None,
            cancelled=lambda:False,max_vlm_calls=3,page_budget_seconds=30,vlm_budget_seconds=None,ocr_backend=None,
            force_ocr=False,ignore_pdf_text=False):
    _,limit=page_budgets(ocr_backend)
    if not 0<page_budget_seconds<=limit: raise ValueError(f'每頁預算必須大於 0 且不超過 {limit} 秒')
    if vlm_budget_seconds is not None and not 30<=vlm_budget_seconds<=120: raise ValueError('VLM 完整模式預算須為 30–120 秒')
    output=clone(doc)
    for key in ANALYSIS_COLLECTIONS: output[key]=[]
    app=Path(__file__).resolve().parents[1]
    for page in output['pages']:
        if cancelled(): break
        p=page['number']
        if page_numbers and p not in page_numbers: continue
        started=time.monotonic()
        # Reserve 1.5 seconds for process reap, checkpoint read and result delivery.
        actual_budget=vlm_budget_seconds if use_vlm and max_vlm_calls>0 and vlm_budget_seconds else page_budget_seconds
        work_seconds=max(.05,actual_budget-1.5)
        run_id=uid('budget'); root=Path(data_root)/'traces'/run_id; root.mkdir(parents=True,exist_ok=True)
        request={'doc':doc,'data_root':str(data_root),'options':{'page_numbers':[p],'crop':crop,
            'rotation':rotation,'use_vlm':use_vlm and max_vlm_calls>0,'model':model,'host':host,
            'max_vlm_calls':1},'work_seconds':work_seconds}
        if ocr_backend is not None: request['options']['ocr_backend']=ocr_backend
        if force_ocr: request['force_ocr']=True
        if ignore_pdf_text: request['options']['ignore_pdf_text']=True
        atomic_json(root/'request.json',request)
        progress(f'第 {p} 頁：OCR／工程解析'+(f'／{model}' if use_vlm and max_vlm_calls>0 else '')+f'（本方法 {actual_budget} 秒上限，可取消）')
        status=supervise([sys.executable,'-m','workbench.page_worker',str(root)],cwd=app,
            deadline=started+work_seconds,cancelled=cancelled,log_path=root/'worker.log')
        result_path=root/'result.json' if (root/'result.json').exists() else root/'checkpoint.json'
        result=json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else None
        if result:
            for key in ANALYSIS_COLLECTIONS: output[key].extend(result.get(key,[]))
            new_page=next(x for x in result['pages'] if x['number']==p); page.update(new_page)
        else: page['processing']='failed'
        if status!='complete':
            page['processing']=status
            output['warnings'].append(f'第 {p} 頁：'+('已取消' if status=='cancelled' else '未完成識圖，保留已取得候選；請檢查影像或局部重讀'))
        if status=='complete' and page['processing']!='complete': status='failed'
        elapsed=round(time.monotonic()-started,3)
        page['analysis_seconds']=elapsed; page['deadline_seconds']=actual_budget
        page['recognition_status']=status
        run={'id':run_id,'page':p,'type':'page_budget','status':status,'at':now(),
            'elapsed_seconds':elapsed,'deadline_seconds':actual_budget,'trace_dir':str(root),
            'timing_scope':'rendered page -> worker start -> OCR -> extraction -> checkpoint delivery',
            'accuracy_verified':False}
        if ocr_backend is not None:
            run['requested_ocr']=ocr_backend
        if ignore_pdf_text:
            run['ignore_pdf_text']=True
        output['tool_runs'].append(run); atomic_json(root/'budget.json',run)
        if use_vlm: max_vlm_calls-=1
        progress(f'第 {p} 頁：{elapsed:.2f} 秒，'+('辨識流程完成，內容待覆核' if status=='complete' else '未完成，需處理'))
        if status=='cancelled': break
    output['cancelled']=cancelled()
    output['replace_generated_pages']=[p['number'] for p in output['pages'] if p.get('recognition_status')=='complete'
        and any(r.get('type')=='page_budget' and r['page']==p['number'] for r in output['tool_runs'])] if crop is None else []
    return output

def analyze(doc,data_root,*,page_numbers=None,crop=None,rotation=0,use_vlm=False,
            model='qwen2.5vl:7b',host='http://localhost:11434',progress=lambda _:None,
            cancelled=lambda:False,max_vlm_calls=3,page_budget_seconds=30,vlm_budget_seconds=None,ocr_backend=None,
            force_ocr=False):
    selected=list(dict.fromkeys(x.strip() for x in model.split(',') if x.strip()))
    if use_vlm and (not selected or len(selected)>4): raise ValueError('請指定 1–4 個本機 vision 模型，名稱以逗號分隔')
    selected=selected if use_vlm else [model]
    compare=ocr_backend=='compare_mineru'
    if compare:
        passes=[('rapid_auto', use_vlm, name) for name in selected]
        passes.append(('mineru_basic', False, model))
    else:
        passes=[(ocr_backend, use_vlm, name) for name in selected]
    combined=None
    for index,(backend, with_vlm, name) in enumerate(passes):
        if cancelled(): break
        from .ocr_engines import BACKENDS
        description=BACKENDS.get(backend, 'PDF／OCR 與工程解析')
        if with_vlm:
            description+=' ＋ '+name
        progress(f'方法 {index+1}/{len(passes)}：'+description)
        budget=page_budget_seconds
        if budget==30:
            budget,_=page_budgets(backend)
        backend_options={'ocr_backend':backend} if backend is not None else {}
        if compare or force_ocr:
            backend_options['force_ocr']=True
        if force_ocr:
            backend_options['ignore_pdf_text']=True
        output=_analyze_single(doc,data_root,page_numbers=page_numbers,crop=crop,rotation=rotation,
            use_vlm=with_vlm,model=name,host=host,progress=progress,cancelled=cancelled,
            max_vlm_calls=max_vlm_calls,page_budget_seconds=budget,vlm_budget_seconds=vlm_budget_seconds,**backend_options)
        for run in output['tool_runs']: run['method_pass']=index+1
        if combined is None: combined=output
        else:
            for key in ANALYSIS_COLLECTIONS: combined.setdefault(key,[]).extend(output.get(key,[]))
            # A later model timeout does not discard a complete earlier pass;
            # it remains visible as an incomplete method in the trace.
            for page in combined['pages']:
                other=next(p for p in output['pages'] if p['number']==page['number'])
                if page.get('recognition_status')!='complete' and other.get('recognition_status')=='complete': page.update(other)
            combined['replace_generated_pages']=sorted(set(combined.get('replace_generated_pages',[]))|
                                                        set(output.get('replace_generated_pages',[])))
    if combined is None:
        combined=clone(doc)
        for key in ANALYSIS_COLLECTIONS: combined[key]=[]
    combined['cancelled']=cancelled()
    return combined
