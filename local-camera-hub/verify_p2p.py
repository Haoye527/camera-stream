import ctypes as c,json,pathlib,os,time,struct,threading,hmac,hashlib,base64,secrets,ast,argparse,subprocess
p=pathlib.Path(__file__).parent
parser=argparse.ArgumentParser()
parser.add_argument('--mode',type=int,default=203)
parser.add_argument('--quality',type=int,default=0)
parser.add_argument('--seconds',type=int,default=60)
parser.add_argument('--tag',default='tcp')
parser.add_argument('--hls',action='store_true')
parser.add_argument('--workdir',default=str(p))
parser.add_argument('--config',default=str(p/'camera-private.json'))
args=parser.parse_args()
cfg=json.loads(pathlib.Path(args.config).read_text())
p=pathlib.Path(args.workdir).resolve();p.mkdir(parents=True,exist_ok=True)
app=pathlib.Path(r'C:\Program Files (x86)\YIIOTHomePCClientIntl')
os.add_dll_directory(str(app))
d=c.CDLL(str(app/'PPPP_API.dll')); crypto=c.CDLL(str(app/'libcrypto-1_1.dll'))
os.chdir(p)
d.PPPP_Set_LogFilename.argtypes=[c.c_char_p]
d.PPPP_Set_LogFilename(b'p2p-private-sdk.log')
d.PPPP_Config_Debug.argtypes=[c.c_byte,c.c_uint32]
d.PPPP_Config_Debug(0,0)
class AES_KEY(c.Structure):_fields_=[('rd_key',c.c_uint32*60),('rounds',c.c_int)]
crypto.AES_set_decrypt_key.argtypes=[c.c_char_p,c.c_int,c.POINTER(AES_KEY)]
crypto.AES_ecb_encrypt.argtypes=[c.c_char_p,c.c_char_p,c.POINTER(AES_KEY),c.c_int]
for name,argtypes in {
 'PPPP_Initialize':[c.c_char_p,c.c_int],
 'PPPP_WakeUp_And_Connect':[c.c_char_p,c.c_byte,c.c_int,c.c_char_p,c.c_char_p],
 'PPPP_Check':[c.c_int,c.c_char_p],
 'PPPP_Read':[c.c_int,c.c_byte,c.c_char_p,c.POINTER(c.c_int),c.c_int],
 'PPPP_Write':[c.c_int,c.c_byte,c.c_char_p,c.c_int],
 'PPPP_Close':[c.c_int],'PPPP_DeInitialize':[]}.items():
 fn=getattr(d,name);fn.argtypes=argtypes;fn.restype=c.c_int
report={'source':'LocalCameraHub adapted bounded original-stream test','mode':args.mode,'quality':args.quality,'pointer_bits':c.sizeof(c.c_void_p)*8,'frames':0,'bytes':0,'channels':{},'responses':[]}
sid=-1; stop=threading.Event();lock=threading.Lock();threads=[];out=None;seq=None;ff=None;fflog=None
def save():
 (p/'compatibility-result.json').write_text(json.dumps(report,indent=2))
 (p/(args.tag+'-result.json')).write_text(json.dumps(report,indent=2))
 with (p/'compatibility-history.jsonl').open('a') as f:f.write(json.dumps(report)+'\n')
 print(json.dumps(report),flush=True)
try:
 report['initialize']=d.PPPP_Initialize(b'\0',12);print('SDK initialization:',report['initialize'],flush=True)
 start=time.time()
 for attempt in range(2):
  sid=d.PPPP_WakeUp_And_Connect(cfg['uid'].encode(),c.c_byte(args.mode),0,cfg['init_string'].encode(),cfg['license'].encode())
  if sid>=0:break
  if attempt==0:time.sleep(5)
 report['connect']=sid;report['connect_seconds']=round(time.time()-start,2)
 print('P2P connection result:',sid,flush=True)
 if sid<0:raise RuntimeError('P2P connection failed')
 report['check']=d.PPPP_Check(sid,c.create_string_buffer(256))
 key=AES_KEY();crypto.AES_set_decrypt_key((cfg['password']+'0').encode(),128,c.byref(key))
 out=open(p/(args.tag+'-original.h264'),'wb')
 if args.hls:
  hls=p/'live-hls';hls.mkdir(exist_ok=True)
  fflog=(p/'ffmpeg-live.log').open('ab')
  exe=r'C:\Users\Administrator\AppData\Local\Programs\Open Design\resources\app\node_modules\@ffmpeg-installer\win32-x64\ffmpeg.exe'
  ff=subprocess.Popen([exe,'-hide_banner','-loglevel','warning','-y','-fflags','+genpts','-probesize','65536','-analyzeduration','500000','-use_wallclock_as_timestamps','1','-f','h264','-i','pipe:0','-an','-vsync','1','-r','20','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-pix_fmt','yuv420p','-g','40','-sc_threshold','0','-f','hls','-hls_time','2','-hls_list_size','6','-hls_flags','delete_segments+omit_endlist+program_date_time','-start_number',str(int(time.time())),'-hls_segment_filename',str(hls/'part%09d.ts'),str(hls/'live.m3u8')],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=fflog,creationflags=0x08000000)
  report['ffmpeg_pid']=ff.pid
 def frame(n,is_key,data):
  with lock:
   if not args.hls:out.write(data);out.flush()
   if ff:ff.stdin.write(data);ff.stdin.flush()
   report['frames']+=1;report['bytes']+=len(data);report['last_frame_time']=time.time()
 class FrameQueue:
  def __init__(self):self.expected=None;self.buf={};self.lock=threading.Lock();self.running=True;self.started=time.time();self.worker=threading.Thread(target=self.run,daemon=True);self.worker.start()
  def push(self,n,key,data):
   with self.lock:
    self.buf[n]=(key,data,time.time())
    if self.expected is None and key:self.expected=n
    if len(self.buf)>1000:self.buf.pop(next(iter(self.buf)))
  def run(self):
   while self.running:
    item=None
    with self.lock:
     if self.expected in self.buf:
      n=self.expected;item=self.buf.pop(n);self.expected=(n+1)&65535
     elif self.expected is not None:
      keys=[n for n,(key,data,t) in self.buf.items() if key and 0<(n-self.expected)&65535<32768 and time.time()-t>5]
      if keys:self.expected=min(keys,key=lambda n:(n-self.expected)&65535)
    if item:frame(n,item[0],item[1])
    else:time.sleep(.003)
  def stop(self):self.running=False;self.worker.join(2)
 seq=FrameQueue()
 def read_exact(ch,n):
  result=bytearray()
  while len(result)<n and not stop.is_set():
   remaining=n-len(result);b=c.create_string_buffer(remaining);size=c.c_int(remaining)
   ret=d.PPPP_Read(sid,ch,b,c.byref(size),5000)
   report.setdefault('read_status',{})
   rk=str(ch)+':'+str(ret);report['read_status'][rk]=report['read_status'].get(rk,0)+1
   if ret in (0,-3003,-3004) and 0<size.value<=remaining:result.extend(b.raw[:size.value])
   if ret<0 and ret not in (-3003,-3004):return None
  return bytes(result) if len(result)==n else None
 def reader(ch):
  dec=c.create_string_buffer(16)
  while not stop.is_set():
   hdr=read_exact(ch,8)
   if not hdr:continue
   n=struct.unpack_from('>I',hdr,4)[0]
   if not 0<n<1048576:continue
   body=read_exact(ch,n)
   if not body:continue
   report['channels'][str(ch)]=report['channels'].get(str(ch),0)+1
   if ch!=0:
    report.setdefault('packet_shapes',[])
    if len(report['packet_shapes'])<100:report['packet_shapes'].append({'ch':ch,'length':len(body),'sequence':struct.unpack_from('>H',body,6)[0] if len(body)>8 else None,'flag':body[2] if len(body)>2 else None})
   if ch==0:
    if len(body)>=12:
     cmd,resp,number,extra,status=struct.unpack_from('>HHHHi',body)
     report['responses'].append({'cmd':cmd,'response':resp,'status':status,'length':len(body)})
    continue
   if len(body)<24:continue
   raw=bytearray(body)
   if raw[2]&1 and len(raw)>=60:
    for off in (28,44):
     crypto.AES_ecb_encrypt(bytes(raw[off:off+16]),dec,c.byref(key),0);raw[off:off+16]=dec.raw
   seq.push(struct.unpack_from('>H',raw,6)[0],ch==2,bytes(raw[24:]))
 for ch in (0,2,3):
  t=threading.Thread(target=reader,args=(ch,),daemon=True);t.start();threads.append(t)
 nonce=''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789') for _ in range(15))
 sig=base64.b64encode(hmac.new(cfg['password'].encode(),('user=xiaoyiuser&nonce='+nonce).encode(),hashlib.sha1).digest()).decode()[:15]
 auth=(nonce+','+sig).encode().ljust(32,b'\0');payload=bytes([1,args.quality,1,0])
 packet=struct.pack('>BBHI',1,3,0,44)+struct.pack('>HHHH',9029,1,0,4)+auth+payload
 report['request_write']=d.PPPP_Write(sid,0,packet,len(packet))
 stream_start=time.time();end=stream_start+args.seconds
 while args.seconds==0 or time.time()<end:
  if args.hls and (p/'stop.request').exists():break
  report['stream_seconds']=round(time.time()-stream_start,1)
  report['average_fps']=round(report['frames']/max(1,time.time()-stream_start),2)
  (p/(args.tag+'-live-status.json')).write_text(json.dumps(report))
  if ff and ff.poll() is not None:raise RuntimeError('FFmpeg relay exited')
  if args.seconds==0 and time.time()-report.get('last_frame_time',stream_start)>25:raise RuntimeError('Video timeout; reconnect required')
  time.sleep(1)
except Exception as ex:report['error']=str(ex)
finally:
 stop.set()
 for t in threads:t.join(2)
 if seq:seq.stop();time.sleep(.05)
 if out:out.close()
 if ff:
  try:ff.stdin.close();ff.wait(3)
  except Exception:ff.terminate()
 if fflog:fflog.close()
 if sid>=0:d.PPPP_Close(sid)
 d.PPPP_DeInitialize();save()
