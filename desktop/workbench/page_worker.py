"""One page, no UI, no database writes. Parent owns the process deadline."""
import json
import sys
import time
from pathlib import Path
from .bounded import atomic_json

def main():
    root=Path(sys.argv[1]); request=json.loads((root/'request.json').read_text(encoding='utf-8'))
    from .extraction import analyze
    from .ocr_runtime import configure
    deadline=time.monotonic()+request['work_seconds']-1
    backend=request['options'].pop('ocr_backend','rapid_v6')
    base_backend={'rapid_auto':'rapid_v6','indra_auto':'indra'}.get(backend,backend)
    configure(base_backend,force_ocr=True) if request.get('force_ocr') else configure(base_backend)
    rotation_note=None
    if backend in ('rapid_auto','indra_auto') and not request['options'].get('rotation'):
        from PIL import Image
        from .ocr_engines import orientation
        from .paths import resolve_path
        pnum=request['options']['page_numbers'][0]
        page=next(p for p in request['doc']['pages'] if p['number']==pnum)
        if page.get('pdf_image_boxes')!=[]:
            with Image.open(resolve_path(page['image_path'])) as image:
                angle,probes=orientation(image.convert('RGB'))
            request['options']['rotation']=angle
            rotation_note={'requested_ocr':backend,'rotation':angle,'orientation_probes':probes}
    output=analyze(request['doc'],request['data_root'],**request['options'],
        deadline=deadline,checkpoint=lambda d:atomic_json(root/'checkpoint.json',d))
    if rotation_note:
        output['warnings'].append('四向比對採用 '+str(rotation_note['rotation'])+'°；方向與內容仍須人工核對')
        for run in output['tool_runs']: run.update(rotation_note)
    atomic_json(root/'result.json',output)

if __name__=='__main__': main()
