"""Resolve relocated asset files without rewriting signed/audited document JSON."""
import json
import os
from pathlib import Path,PureWindowsPath

def resolve_path(value):
    path=Path(value); root=os.environ.get('DRAWIQ_DATA_ROOT')
    if not root: return path
    root=Path(root).resolve(); mapping=root/'path_map.json'
    if not mapping.exists(): return path
    config=json.loads(mapping.read_text(encoding='utf-8'))
    for original in config.get('original_roots',[]):
        try: relative=PureWindowsPath(str(value)).relative_to(PureWindowsPath(original))
        except ValueError: continue
        if '..' in relative.parts: raise ValueError('資料移轉路徑不可跳出工作區')
        resolved=root.joinpath(*relative.parts).resolve()
        if not resolved.is_relative_to(root): raise ValueError('資料移轉路徑不可跳出工作區')
        return resolved
    return path
