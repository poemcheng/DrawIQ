"""STEP measurement in a bounded worker, using fixed code only."""
import json
import subprocess
import sys
from pathlib import Path

def measure_step(path, timeout=45):
    path=Path(path).resolve()
    if path.suffix.lower() not in ('.step','.stp') or not path.is_file(): raise ValueError('請選擇 STEP／STP 檔案')
    proc=subprocess.run([sys.executable,str(Path(__file__).with_name('geometry_worker.py')),str(path)],capture_output=True,text=True,encoding='utf-8',timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if proc.returncode: raise ValueError('STEP 量測失敗：'+(proc.stderr or proc.stdout)[-2000:])
    # OCP may print an import log; the final line is our structured result.
    return json.loads(proc.stdout.strip().splitlines()[-1])

def compare_steps(candidate,reference):
    a=measure_step(candidate); b=measure_step(reference)
    if not a['valid'] or not b['valid']: raise ValueError('候選與參考 STEP 必須先通過 B-rep 有效性檢查')
    return {'candidate':a,'reference':b,'volume_relative_error':abs(a['volume']-b['volume'])/b['volume'] if b['volume'] else None,
        'bbox_absolute_errors':[abs(x-y) for x,y in zip(a['bbox_extents'],b['bbox_extents'])],
        'limitation':'包圍盒與體積不是完整幾何等價證明；尚未做 ICP／特徵配對，旋轉不同會影響包圍盒。單位須由提供者確認。'}
