"""One cancellable local OCR process per crop. Never changes document data."""
import sys
from pathlib import Path
from PIL import Image, ImageOps
from .ingest import LEGACY
from .bounded import atomic_json


def main(folder):
    if str(LEGACY) not in sys.path: sys.path.insert(0, str(LEGACY))
    from .ocr_runtime import configure
    from .region_capture import classify
    configure()
    from drawing_intelligence import ocr
    # User-selected rotation defines the crop orientation. A short "90°" or
    # digit-only line must not be flipped 180° by the text-line classifier.
    ocr._rapidocr_instance.use_cls = False
    output = ocr.recognize_page(folder/'input.png')
    padding = 0
    with Image.open(folder/'input.png') as image:
        w, h = image.size
        if not output.regions:
            border = 16
            retry = ocr._run_rapidocr(ImageOps.expand(image.convert('RGB'), border=border, fill='white'), 1)
            if retry.regions:
                output, padding = retry, border
                output.warnings.append('局部首次未讀到文字，已補白邊重試')
    tokens = []
    for r in output.regions:
        box = [max(0,min(1,(float(v)-padding)/d)) for v,d in zip(r.box,(w,h,w,h))]
        if box[0] < box[2] and box[1] < box[3]:
            source = ocr._drawiq_engine if padding else r.source
            tokens.append(dict(text=r.text, box=box, confidence=float(r.confidence), source=source))
    atomic_json(folder/'worker_result.json', dict(status='complete' if output.text.strip() else 'empty',
        text=output.text, tokens=tokens, candidates=classify(output.text,tokens), warnings=output.warnings))


if __name__ == '__main__': main(Path(sys.argv[1]))
