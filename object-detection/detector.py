"""Local ONNX detection. Native recording-plan event types control each channel."""
import sys
import contextlib, datetime as dt, json, os, pathlib, sqlite3, subprocess, threading, time, uuid
import numpy as np
from PIL import Image, ImageDraw
import onnxruntime as ort

ROOT=pathlib.Path(__file__).resolve().parent
DB=pathlib.Path(r'C:\EasyNVR\configs\data.db')
RECORDS=pathlib.Path(r'C:\EasyNVR\r\easynvr_rec')
FFMPEG=pathlib.Path(r'C:\Users\Administrator\AppData\Local\Programs\Open Design\resources\app\node_modules\@ffmpeg-installer\win32-x64\ffmpeg.exe')
sys.path.insert(0,str(ROOT.parent))
from camera_registry import Registry,channel_lock
TRACKING=json.loads((ROOT/'tracking/settings.json').read_text(encoding='utf8'))
CAPTURE_SIZE=(1280,720) if TRACKING['enabled'] else (640,360)
LABELS={0:'person',1:'bicycle',2:'car',3:'motorcycle',5:'bus',6:'train',7:'truck',14:'bird',15:'cat',16:'dog',17:'horse',18:'sheep',19:'cow',20:'elephant',21:'bear',22:'zebra',23:'giraffe'}
def group(category):
    if category==0:return '1'
    if category in (1,2,3,5,6,7):return '2'
    if 14<=category<=23:return '3'
    return None

def nms(boxes,scores,threshold=.5):
    order=np.argsort(scores)[::-1];keep=[]
    while len(order):
        i=int(order[0]);keep.append(i);rest=order[1:]
        if not len(rest):break
        lt=np.maximum(boxes[i,:2],boxes[rest,:2]);rb=np.minimum(boxes[i,2:],boxes[rest,2:])
        inter=np.prod(np.maximum(rb-lt,0),axis=1)
        area=np.prod(np.maximum(boxes[:,2:]-boxes[:,:2],0),axis=1)
        overlap=inter/np.maximum(area[i]+area[rest]-inter,1e-6)
        order=rest[overlap<=threshold]
    return keep

class YoloXModel:
    name='YOLOX-S OpenCV Zoo'
    source='local-yolox'
    def __init__(self,path=ROOT/'models/yolox.onnx'):
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
        self.input=self.session.get_inputs()[0].name
        grids=[];strides=[]
        for stride in (8,16,32):
            x,y=np.meshgrid(np.arange(640//stride),np.arange(640//stride))
            grids.append(np.stack((x,y),axis=-1).reshape(-1,2));strides.extend([stride]*x.size)
        self.grids=np.concatenate(grids);self.strides=np.array(strides)[:,None]
    def infer(self,image,threshold=.5):
        ratio=min(640/image.width,640/image.height);size=(int(image.width*ratio),int(image.height*ratio))
        canvas=Image.new('RGB',(640,640),(114,114,114));canvas.paste(image.resize(size),(0,0))
        tensor=np.asarray(canvas,dtype=np.float32).transpose(2,0,1)[None]
        raw=self.session.run(None,{self.input:tensor})[0][0]
        xy=(raw[:,:2]+self.grids)*self.strides;wh=np.exp(np.clip(raw[:,2:4],-20,20))*self.strides
        boxes=np.concatenate((xy-wh/2,xy+wh/2),axis=1)/ratio
        scores=raw[:,4:5]*raw[:,5:];categories=np.argmax(scores,axis=1);confidence=np.max(scores,axis=1)
        result=[]
        for category in LABELS:
            ids=np.flatnonzero((categories==category)&(confidence>=threshold))
            for k in nms(boxes[ids],confidence[ids]):
                i=ids[k];box=boxes[i].copy();box[[0,2]]=np.clip(box[[0,2]],0,image.width);box[[1,3]]=np.clip(box[[1,3]],0,image.height)
                result.append({'class':category,'label':LABELS[category],'type':group(category),'confidence':float(confidence[i]),'box':box.tolist()})
        return result

class Yolo26Model:
    name='YOLO26s ONNX'
    source='local-yolo26s'
    def __init__(self,path=ROOT/'models/yolo26s.onnx'):
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
        self.input=self.session.get_inputs()[0].name
        shape=self.session.get_inputs()[0].shape
        if shape!=[1,3,640,640]:raise ValueError(f'Unexpected YOLO26 input: {shape}')
        output=self.session.get_outputs()[0].shape
        if len(output)!=3 or output[-1]!=6:raise ValueError(f'Expected end-to-end YOLO26 output [1,N,6], got {output}')
    @staticmethod
    def preprocess(image):
        image=image.convert('RGB');ratio=min(640/image.width,640/image.height)
        size=(round(image.width*ratio),round(image.height*ratio))
        left=(640-size[0])//2;top=(640-size[1])//2
        canvas=Image.new('RGB',(640,640),(114,114,114))
        canvas.paste(image.resize(size,Image.Resampling.BILINEAR),(left,top))
        tensor=np.ascontiguousarray(np.asarray(canvas,dtype=np.float32).transpose(2,0,1)[None]/255.)
        return tensor,ratio,left,top
    @staticmethod
    def decode(raw,image,ratio,left,top,threshold):
        result=[]
        for row in raw:
            if not np.isfinite(row).all() or row[4]<threshold:continue
            category=int(row[5])
            if category not in LABELS:continue
            box=np.asarray(row[:4],dtype=np.float32).copy()
            box[[0,2]]=(box[[0,2]]-left)/ratio;box[[1,3]]=(box[[1,3]]-top)/ratio
            box[[0,2]]=np.clip(box[[0,2]],0,image.width);box[[1,3]]=np.clip(box[[1,3]],0,image.height)
            if box[2]<=box[0] or box[3]<=box[1]:continue
            result.append({'class':category,'label':LABELS[category],'type':group(category),'confidence':float(row[4]),'box':box.tolist()})
        return result
    def infer(self,image,threshold=.5):
        tensor,ratio,left,top=self.preprocess(image)
        raw=self.session.run(None,{self.input:tensor})[0][0]
        return self.decode(raw,image,ratio,left,top,threshold)

def Model():
    config=json.loads((ROOT/'model-config.json').read_text(encoding='utf8'))
    kind=config['model']
    if kind=='yolo26s':return Yolo26Model()
    if kind=='yolox':return YoloXModel()
    raise ValueError(f'Unsupported detection model: {kind}')

def plan_types(connection,channel,now):
    row=connection.execute('select p.enabled,p.plans,p.event_types from record_plans p join record_with_channels c on c.plan_id=p.id where c.channel_id=?',(channel,)).fetchone()
    if not row or not row[0]:return set()
    schedule=row[1].decode() if isinstance(row[1],bytes) else row[1]
    if not schedule or len(schedule)!=168 or schedule[now.weekday()*24+now.hour]!='1':return set()
    return {str(item['type']) for item in json.loads(row[2] or '[]')}&{'1','2','3'}

def capture_command(url,enhanced=None):
    enhanced=TRACKING['enabled'] if enhanced is None else enhanced
    live=['-live_start_index','-1'] if url.split('?',1)[0].lower().endswith('.m3u8') else []
    vf='fps=2,scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2' if enhanced else 'fps=1,scale=640:360'
    return [str(FFMPEG),'-nostdin','-loglevel','error','-rw_timeout','15000000',
        '-probesize','512000','-analyzeduration','1000000',*live,'-i',url,'-an',
        '-vf',vf,'-pix_fmt','rgb24','-f','rawvideo','pipe:1']

class Capture:
    def __init__(self,url):
        self.url=url;self.stop=threading.Event();self.frame=None;self.at=0.;self.error=None;self.process=None;self.progress=time.time()
        threading.Thread(target=self.run,daemon=True).start()
        threading.Thread(target=self.watchdog,daemon=True).start()
    def watchdog(self):
        while not self.stop.wait(2):
            process=self.process
            if process and process.poll() is None and time.time()-self.progress>30:
                self.error='取帧超时，正在重连'
                try:process.kill()
                except OSError:pass
    def run(self):
        while not self.stop.is_set():
            process=None
            try:
                command=capture_command(self.url)
                process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                self.progress=time.time();self.process=process
                while True:
                    size=CAPTURE_SIZE[0]*CAPTURE_SIZE[1]*3;data=process.stdout.read(size)
                    if len(data)!=size:raise RuntimeError('视频流断开，等待重连')
                    self.frame=Image.frombytes('RGB',CAPTURE_SIZE,data);self.at=time.time();self.progress=self.at;self.error=None
            except Exception as error:self.error=str(error)
            finally:
                if process:
                    process.kill();process.wait();process.stdout.close()
                self.process=None
            self.stop.wait(5)
    def close(self):
        self.stop.set()
        if self.process:
            try:self.process.kill()
            except OSError:pass

class Events:
    def __init__(self,db=DB,records=RECORDS,source='local-yolox',grace_seconds=10):self.db=db;self.records=pathlib.Path(records);self.states={};self.source=source;self.grace_seconds=grace_seconds
    def update(self,channel,enabled,detections,image,now):
        with channel_lock(channel):
            for kind in ('1','2','3'):
                key=(channel,kind);state=self.states.get(key);hits=[d for d in detections if d['type']==kind] if kind in enabled else []
                if hits:
                    if state is None:
                        first=now
                        if kind=='1':
                            for hit in hits:
                                if hit.get('track_confirmed') and hit.get('first_seen'):
                                    first=min(first,max(now-dt.timedelta(seconds=2),dt.datetime.fromisoformat(hit['first_seen'])))
                        state={'first':first,'last':now,'hits':0,'id':None};self.states[key]=state
                    state['hits']+=1;state['last']=now
                    if state['hits']>=2 or any(h.get('track_confirmed') for h in hits):
                        with contextlib.closing(sqlite3.connect(self.db,timeout=5)) as connection:
                            if state['id'] is None:
                                device,number=channel.rsplit('_',1);relative=pathlib.Path(device)/number/(now.strftime('%Y%m%d')+'_event')/(now.strftime('%Y%m%d%H%M%S')+'_ai_'+kind+'_'+uuid.uuid4().hex[:8]+'.jpg')
                                target=(self.records/relative).resolve()
                                if not target.is_relative_to(self.records.resolve()):raise ValueError('Invalid channel path')
                                target.parent.mkdir(parents=True,exist_ok=True);picture=image.copy();draw=ImageDraw.Draw(picture)
                                for hit in hits:
                                    draw.rectangle(hit['box'],outline='#39d98a',width=2);draw.text(tuple(hit['box'][:2]),hit['label']+' '+str(round(hit['confidence']*100))+'%',fill='#39d98a')
                                picture.save(target,'JPEG',quality=90)
                                stamp=now.isoformat(' ');first=state['first'].isoformat(' ')
                                cursor=connection.execute('insert into events(channel_id,event_type,start_time,trigger_time,last_trigger_time,duration,snapshot_path,description,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?)',(channel,kind,first,first,stamp,max(1000,int((now-state['first']).total_seconds()*1000)),str(relative),json.dumps({'source':self.source,'objects':hits},ensure_ascii=False),stamp,stamp))
                                state['id']=cursor.lastrowid
                            else:
                                changed=connection.execute('update events set last_trigger_time=?,duration=?,updated_at=? where id=? and channel_id=?',(now.isoformat(' '),max(1000,int((now-state['first']).total_seconds()*1000)),now.isoformat(' '),state['id'],channel)).rowcount
                                if not changed:self.states.pop(key,None)
                            connection.commit()
                elif state:
                    elapsed=(now-state['last']).total_seconds()
                    if state['id'] is not None and kind in enabled and image is not None:
                        # Bridge short missed detections in the displayed event interval,
                        # but never invent a fresh trigger or extend through a stream outage.
                        end=min(now,state['last']+dt.timedelta(seconds=self.grace_seconds))
                        with contextlib.closing(sqlite3.connect(self.db,timeout=5)) as connection:
                            changed=connection.execute('update events set duration=max(duration,?),updated_at=? where id=? and channel_id=?',
                                (max(1000,int((end-state['first']).total_seconds()*1000)),now.isoformat(' '),state['id'],channel)).rowcount
                            connection.commit()
                        if not changed:self.states.pop(key,None)
                    if state['id'] is None or kind not in enabled or elapsed>=self.grace_seconds:self.states.pop(key,None)

def main():
    from tracking import PersonPipeline
    model=Model();writer=Events(source=model.source,grace_seconds=TRACKING['event_grace_seconds'] if TRACKING['enabled'] else 10);captures={};seen={};pipelines={};next_analysis={};inventory=[];next_inventory=0.;state={'model':model.name,'tracking':'ByteTrack' if TRACKING['enabled'] else None,'provider':'CPUExecutionProvider','capture_fps':2 if TRACKING['enabled'] else 1,'hls_start_index':-1,'ready':True,'pid':os.getpid(),'cameras':{}}
    (ROOT/'process.json').write_text(json.dumps({'pid':os.getpid()}))
    while True:
        if time.monotonic()>=next_inventory:
            inventory=Registry(DB,RECORDS).list(history=False,private=True);next_inventory=time.monotonic()+5
            desired={r['channel_id']:r.get('source_url','') for r in inventory if r['enabled'] and r.get('source_url','').startswith(('http://','https://','rtsp://'))}
            for channel in list(captures):
                if channel not in desired or captures[channel].url!=desired[channel]:captures.pop(channel).close();state['cameras'].pop(channel,None);seen.pop(channel,None);pipelines.pop(channel,None);next_analysis.pop(channel,None)
            for channel,url in desired.items():
                if channel not in captures:captures[channel]=Capture(url)
            for channel in list(state['cameras']):
                if channel not in {r['channel_id'] for r in inventory if r['enabled']}:state['cameras'].pop(channel,None)
            for camera in inventory:
                if camera['enabled'] and camera['channel_id'] not in desired:state['cameras'][camera['channel_id']]={'error':'该通道尚未配置可用视频源','enabled_types':[]}
        for channel,capture in list(captures.items()):
            now=dt.datetime.now().astimezone();status=state['cameras'].setdefault(channel,{})
            status.update(frame_age=round(time.time()-capture.at,1) if capture.at else None,error=capture.error)
            try:
                with contextlib.closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True,timeout=3)) as connection:enabled=plan_types(connection,channel,now)
                status['enabled_types']=sorted(enabled)
                if not enabled:
                    writer.update(channel,enabled,[],None,now)
                    if channel in pipelines:pipelines[channel].reset()
                    continue
                if not capture.frame or time.time()-capture.at>15:
                    status['error']=capture.error or '等待新视频帧';writer.update(channel,enabled,[],None,now)
                    if channel in pipelines:pipelines[channel].reset()
                    continue
                if seen.get(channel)==capture.at:continue
                if time.monotonic()<next_analysis.get(channel,0):continue
                seen[channel]=capture.at;image=capture.frame;started=time.perf_counter()
                if TRACKING['enabled']:
                    if channel not in pipelines:pipelines[channel]=PersonPipeline(model,TRACKING)
                    pipeline=pipelines[channel];detections=pipeline.process(image,now.timestamp(),'1' in enabled)
                    rate=TRACKING['active_fps'] if pipeline.active(now.timestamp()) else TRACKING['idle_fps']
                    next_analysis[channel]=started+1/rate;status['tracking']=pipeline.stats;status['target_analysis_fps']=rate
                else:detections=model.infer(image)
                writer.update(channel,enabled,detections,image,now)
                status.update(last_inference=now.isoformat(),inference_ms=round((time.perf_counter()-started)*1000),objects=[{k:v for k,v in d.items() if k!='box'} for d in detections],error=None)
            except Exception as error:status['error']=str(error)
        state['updated_at']=dt.datetime.now().astimezone().isoformat()
        temporary=ROOT/f'status-{os.getpid()}.tmp'
        try:
            temporary.write_text(json.dumps(state,ensure_ascii=False),encoding='utf8')
            for attempt in range(5):
                try:temporary.replace(ROOT/'status.json');break
                except PermissionError:
                    if attempt==4:raise
                    time.sleep(.1)
        except OSError as error:
            # A reader/antivirus may briefly hold the Windows destination file.
            # Status publication must never terminate detection or event writing.
            print(f'Status update deferred: {error}',file=sys.stderr,flush=True)
        time.sleep(.1 if TRACKING['enabled'] else .5)

if __name__=='__main__':main()
