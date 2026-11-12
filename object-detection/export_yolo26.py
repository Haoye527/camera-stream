"""One-time export; run with .export-venv, not the production detector venv."""
import hashlib,json,pathlib
from ultralytics import YOLO

root=pathlib.Path(__file__).resolve().parent
weights=root/'models/yolo26s.pt'
expected='646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b'
assert hashlib.sha256(weights.read_bytes()).hexdigest()==expected,'Unexpected official weights hash'
model=YOLO(str(weights))
output=model.export(format='onnx',imgsz=640,batch=1,dynamic=False,half=False,
                    simplify=False,opset=17,nms=False,device='cpu')
path=pathlib.Path(output)
(root/'models/yolo26s-manifest.json').write_text(json.dumps({
    'source':'https://huggingface.co/Ultralytics/YOLO26/resolve/main/yolo26s.pt',
    'weights_sha256':expected,'onnx_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
    'onnx_bytes':path.stat().st_size,'input':[1,3,640,640],
    'export':'FP32, opset17, end-to-end, CPU, batch1',
},indent=2),encoding='utf8')
print(output)
