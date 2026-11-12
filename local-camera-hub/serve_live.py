import pathlib,subprocess,time,json,threading,sys,argparse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
script_root=pathlib.Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('quality',nargs='?',type=int,default=1);parser.add_argument('--port',type=int,default=18767);parser.add_argument('--workdir',default=str(script_root));parser.add_argument('--config',default=str(script_root/'camera-private.json'));args=parser.parse_args()
p=pathlib.Path(args.workdir).resolve();p.mkdir(parents=True,exist_ok=True);root=p/'live-hls';root.mkdir(exist_ok=True)
state={'status':'connecting','url':f'http://127.0.0.1:{args.port}/live.m3u8'}
quality=args.quality
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  name=self.path.split('?')[0].lstrip('/')
  if name=='status':
   try:state.update(json.loads((p/'live-live-status.json').read_text()))
   except (OSError,ValueError):pass
   state['status']='live' if time.time()-state.get('last_frame_time',0)<10 else 'connecting'
   data=json.dumps(state).encode();mime='application/json'
  elif name=='live.m3u8' or (name.startswith('part') and name.endswith('.ts') and name[4:-3].isdigit()):
   file=root/name
   if not file.is_file() or (name=='live.m3u8' and time.time()-file.stat().st_mtime>20):self.send_error(503,'Stream reconnecting');return
   try:data=file.read_bytes()
   except OSError:self.send_error(404);return
   mime='application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'video/mp2t'
  else:self.send_error(404);return
  self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(data)
server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
info={'pid':__import__('os').getpid(),'url':state['url'],'quality':quality}
(p/'live-process.json').write_text(json.dumps(info))
child=None
try:
 while not (p/'stop.request').exists():
  for old in root.glob('part*.ts'):
   if time.time()-old.stat().st_mtime>120:old.unlink(missing_ok=True)
  with (p/'relay-private.log').open('ab') as log:
   child=subprocess.Popen([sys.executable,str(script_root/'verify_p2p.py'),'--mode','75','--quality',str(quality),'--seconds','0','--tag','live','--hls','--workdir',str(p),'--config',str(pathlib.Path(args.config).resolve())],stdout=log,stderr=log,creationflags=0x08000000)
   info['worker_pid']=child.pid;(p/'live-process.json').write_text(json.dumps(info))
   child.wait()
  state['status']='reconnecting';time.sleep(4)
finally:
 if child and child.poll() is None:child.terminate()
 server.shutdown()
