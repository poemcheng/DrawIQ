"""Explicit one-time official model download, pinned revision and SHA-256 manifest."""
import hashlib
import json
from pathlib import Path
from huggingface_hub import HfApi, hf_hub_download

ROOT=Path(__file__).resolve().parents[1]
def main():
    repo='datalab-to/indra-ocr'; revision='v1.1.0'
    commit=HfApi().model_info(repo,revision=revision).sha
    target=ROOT/'models'/'indra-v1.1.0'
    names=['det.onnx','rec.onnx','rec_cjk.onnx','rec_world.onnx','rec_rtl.onnx',
           'order.onnx','orient.onnx','script.onnx','meta.json','LICENSE','README.md']
    hashes={}
    for name in names:
        print('Installing '+name,flush=True)
        path=Path(hf_hub_download(repo,name,revision=commit,local_dir=target))
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (target/'manifest.json').write_text(json.dumps(dict(repository=repo,revision=revision,commit=commit,
        files=hashes),ensure_ascii=False,indent=2),encoding='utf-8')
    print('Complete: '+str(target))
if __name__=='__main__': main()
