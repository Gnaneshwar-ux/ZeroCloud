"""Initialize a selected desktop library using original folders in place."""
import argparse
from collections import Counter
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'engine'))
from portable_engine import Engine

parser = argparse.ArgumentParser()
parser.add_argument('--data', required=True)
parser.add_argument('--source', required=True)
parser.add_argument('--models')
parser.add_argument('--enable-faces', action='store_true')
args = parser.parse_args()
engine = Engine(args.data)
try:
    engine.paused = True
    if args.models:
        target = engine.root / 'models' / '.deepface' / 'weights'; target.mkdir(parents=True, exist_ok=True)
        for name in ['face_recognition_sface_2021dec.onnx','haarcascade_eye.xml','haarcascade_frontalface_default.xml']:
            source = Path(args.models) / '.deepface' / 'weights' / name
            if not (target / name).exists(): shutil.copyfile(source,target / name)
    result = engine.dispatch('scan', {'path':args.source})
    print(f"Linked {result['count']:,} original files. No original copies created.",flush=True)
    if result['warnings']: print('Scan warnings:',result['warnings'],flush=True)
    library = engine.dispatch('load', {})
    print('Media counts:',dict(Counter(item['kind'] for item in library['items'])),flush=True)
    if args.enable_faces: engine.dispatch('faceControl', {'enabled':True,'paused':True})
    print('Library data folder:',engine.root,flush=True)
finally:
    engine.dispatch('shutdown', {})
