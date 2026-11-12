"""Offline validation only: never writes production events or changes plans."""
import contextlib,json,sqlite3,statistics,time
from PIL import Image,ImageDraw
from detector import ROOT,DB,RECORDS,YoloXModel,Yolo26Model

report={'models':{},'samples':[]}
samples=[ROOT/'test-bus.jpg',ROOT/'test-dog.jpg']
with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as db:
    channels=db.execute("select distinct channel_id from events where event_type in ('1','2','3')").fetchall()
    for channel, in channels:
        rows=db.execute("select snapshot_path,description from events where channel_id=? and event_type in ('1','2','3') order by id desc limit 300",(channel,)).fetchall()
        def confidence(row):
            try:return max((h.get('confidence',0) for h in json.loads(row[1] or '{}').get('objects',[])),default=0)
            except (ValueError,TypeError):return 0
        rows=rows[:2]+sorted(rows,key=confidence,reverse=True)[:3]
        for relative,_ in rows:
            path=(RECORDS/relative).resolve()
            if path.is_relative_to(RECORDS.resolve()) and path.is_file():samples.append(path)
out=ROOT/'yolo26-validation';out.mkdir(exist_ok=True)
for model in (YoloXModel(),Yolo26Model()):
    timings=[];results=[]
    for index,path in enumerate(samples):
        with Image.open(path) as opened:image=opened.convert('RGB')
        model.infer(image)
        for _ in range(3):
            started=time.perf_counter();hits=model.infer(image);timings.append((time.perf_counter()-started)*1000)
        for hit in hits:
            assert 0<=hit['box'][0]<hit['box'][2]<=image.width
            assert 0<=hit['box'][1]<hit['box'][3]<=image.height
        results.append({'sample':index,'file':str(path),'objects':hits})
        if isinstance(model,Yolo26Model):
            draw=ImageDraw.Draw(image)
            for hit in hits:
                draw.rectangle(hit['box'],outline='#39d98a',width=2)
                draw.text(tuple(hit['box'][:2]),f"{hit['label']} {hit['confidence']:.2f}",fill='#39d98a')
            image.save(out/f'sample-{index}.jpg')
    report['models'][model.name]={'median_ms':round(statistics.median(timings),1),'samples':results}
new=report['models']['YOLO26s ONNX']['samples']
assert any(h['type']=='1' for h in new[0]['objects']),'Bus image: person not detected'
assert any(h['type']=='2' for h in new[0]['objects']),'Bus image: vehicle not detected'
assert any(h['type']=='3' for h in new[1]['objects']),'Dog image: animal not detected'
(out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:{'median_ms':v['median_ms'],'sample_count':len(v['samples']),'detections':[[(h['label'],round(h['confidence'],2)) for h in s['objects']] for s in v['samples']]} for k,v in report['models'].items()},ensure_ascii=False))
