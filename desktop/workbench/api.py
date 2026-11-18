"""Opt-in localhost, read-only integration API for released knowledge."""
import hmac
from pathlib import Path

def create_app(data_root,token):
    if len(token)<24: raise ValueError('API token 長度至少 24 字元')
    from fastapi import FastAPI, Header, HTTPException, Depends
    from .store import Store
    store=Store(data_root)
    from . import __version__
    app=FastAPI(title='DrawIQ engineering corpus and knowledge API',version=__version__)
    def authorize(authorization: str = Header(default='')):
        if not hmac.compare_digest(authorization,'Bearer '+token): raise HTTPException(401,'Unauthorized')
    @app.get('/health')
    def health(): return {'status':'ok','mode':'localhost-read-only','version':__version__}
    @app.get('/releases',dependencies=[Depends(authorize)])
    def releases(): return store.list_releases()
    @app.get('/releases/{release_id}',dependencies=[Depends(authorize)])
    def release(release_id:str):
        listed=next((r for r in store.list_releases() if r['id']==release_id),None)
        if not listed: raise HTTPException(404,'Not found')
        if listed['revoked_reason']: raise HTTPException(410,'Release revoked')
        return store.get_release(release_id)
    @app.get('/search',dependencies=[Depends(authorize)])
    def search(q:str): return store.search(q)
    @app.get('/documents/{document_id}/summary',dependencies=[Depends(authorize)])
    def document_summary(document_id:str):
        from .knowledge import summary
        try: return summary(store.get(document_id))
        except KeyError: raise HTTPException(404,'Not found')
    @app.get('/semantic/status',dependencies=[Depends(authorize)])
    def semantic_status():
        from .retrieval import index_status
        return index_status(store)
    @app.get('/semantic/search',dependencies=[Depends(authorize)])
    def semantic_search(q:str,approved_only:bool=True):
        from .retrieval import search
        try: return search(store,q,approved_only=approved_only)
        except ValueError as exc: raise HTTPException(409,str(exc))
    @app.get('/knowledge/graph',dependencies=[Depends(authorize)])
    def knowledge_graph(include_candidates:bool=False):
        from .knowledge import build_graph
        from .corpus import source_documents
        docs=source_documents(store)
        return build_graph(docs if include_candidates else [d for d in docs if d['status'] in ('approved','released')],approved_only=not include_candidates)
    return app
