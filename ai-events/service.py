"""Local EasyNVR event preview and confirmed, camera-scoped recording cleanup."""
import sys
import contextlib,datetime as dt,hashlib,json,os,pathlib,re,secrets,shutil,sqlite3,stat,subprocess,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from collections import Counter
from urllib.parse import quote,urlsplit,parse_qs

ROOT=pathlib.Path(__file__).resolve().parent
PORT=18768
SOURCE_DB=pathlib.Path(r'C:\EasyNVR\configs\data.db')
RECORD_ROOT=pathlib.Path(r'C:\EasyNVR\r\easynvr_rec')
FFMPEG=pathlib.Path(r'C:\Users\Administrator\AppData\Local\Programs\Open Design\resources\app\node_modules\@ffmpeg-installer\win32-x64\ffmpeg.exe')
sys.path.insert(0,str(ROOT.parent))
from camera_registry import Registry,ordinary
from cleanup import CleanupMixin
from recordings import RecordingMixin
from live import channel_inventory, relay_media
sys.path.insert(0,str(ROOT.parent/'daily-analysis'))
from daily_analysis import DailyService

def text(value):
    if isinstance(value,bytes):
        try:return value.decode('utf8')
        except UnicodeDecodeError:return value.decode('gb18030','replace')
    return value

class App(CleanupMixin,RecordingMixin):
    def __init__(self,root=ROOT,source=SOURCE_DB,records=RECORD_ROOT):
        self.root=pathlib.Path(root);self.source=pathlib.Path(source).resolve();self.records=pathlib.Path(records).resolve()
        self.cache=self.root/'cache';self.cache.mkdir(parents=True,exist_ok=True);self.image_lock=threading.RLock();self.video_lock=threading.RLock()
        self.active_cached_videos=Counter()
        self.storage_lock=threading.RLock();self.storage_cache=None;self.storage_fingerprint=None;self.storage_at=0;self.cleanup_plans={};self.csrf=secrets.token_urlsafe(32)
        self.daily_lock=threading.Lock();self.daily_service=None
        self.retention=None

    def daily(self):
        with self.daily_lock:
            if self.daily_service is None:self.daily_service=DailyService(self,FFMPEG)
            return self.daily_service

    def cameras(self):return Registry(self.source,self.records).list()

    def camera_files(self,channel_id):
        camera=next((r for r in self.cameras() if r['channel_id']==channel_id),None)
        if not camera:raise ValueError('未知摄像头')
        device,channel=camera['device_id'],camera['number'];folder=self.records/device/channel
        if not folder.resolve().is_relative_to(self.records):raise ValueError('录像目录超出范围')
        files=[]
        def ordinary(path):
            try:info=path.lstat()
            except FileNotFoundError:return False
            return not path.is_symlink() and not (getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT)
        # Never follow junctions/symlinks, including those pointing at another camera.
        for parent in (self.records/device,folder):
            if parent.exists() and not ordinary(parent):raise ValueError('录像目录含有链接，无法清理')
        for directory,dirs,names in os.walk(folder,followlinks=False):
            dirs[:]=[name for name in dirs if ordinary(pathlib.Path(directory)/name)]
            for name in names:
                file=pathlib.Path(directory)/name
                if file.suffix.lower() not in ('.mp4','.jpg','.jpeg','.png'):continue
                if not ordinary(file) or not file.resolve().is_relative_to(folder.resolve()):continue
                try:info=file.stat()
                except FileNotFoundError:continue
                files.append({'path':str(file),'size':info.st_size,'mtime':info.st_mtime_ns})
        return files

    def storage(self):
        with self.storage_lock:
            inventory=self.cameras();fingerprint=[(c['channel_id'],c['name'],c['archived']) for c in inventory]
            if self.storage_cache is None or fingerprint!=self.storage_fingerprint or time.monotonic()-self.storage_at>15:
                cameras=[]
                with self.db() as db:
                    for camera in inventory:
                        channel_id=camera['channel_id'];fallback=camera['name']
                        files=self.camera_files(channel_id)
                        cameras.append({'channel_id':channel_id,'name':fallback,'bytes':sum(f['size'] for f in files),'video_bytes':sum(f['size'] for f in files if pathlib.Path(f['path']).suffix.lower()=='.mp4'),'videos':sum(pathlib.Path(f['path']).suffix.lower()=='.mp4' for f in files),'images':sum(pathlib.Path(f['path']).suffix.lower() in ('.jpg','.jpeg','.png') for f in files)})
                self.storage_cache=cameras;self.storage_fingerprint=fingerprint;self.storage_at=time.monotonic()
            location=self.records
            while not location.exists() and location!=location.parent:location=location.parent
            disk=shutil.disk_usage(location)
            return {'disk':{'path':str(self.records),'total':disk.total,'used':disk.used,'free':disk.free},'cameras':self.storage_cache,'csrf':self.csrf,'retention':self.retention.state if self.retention else None}

    def adaptive_recording(self):
        folder=self.root.parent/'adaptive-recording';settings=folder/'settings.json'
        if not settings.is_file():return {'available':False,'enabled':False,'csrf':self.csrf}
        config=json.loads(settings.read_text(encoding='utf8'));status={}
        try:status=json.loads((folder/'status.json').read_text(encoding='utf8'))
        except (OSError,ValueError):pass
        try:age=time.time()-dt.datetime.fromisoformat(status['updated_at']).timestamp()
        except (ValueError,KeyError,TypeError):age=999999
        return {'available':True,'enabled':bool(config['enabled']),'idle_fps':config['idle_fps'],'pre_seconds':config['pre_seconds'],'post_seconds':config['post_seconds'],'healthy':bool(status.get('running')) and age<120,'processing':bool(status.get('current')),'totals':status.get('totals',{}),'errors':sum(bool(c.get('error')) for c in status.get('cameras',{}).values()),'csrf':self.csrf}

    def set_adaptive_recording(self,enabled):
        if type(enabled) is not bool:raise ValueError('开关值必须为布尔类型')
        folder=self.root.parent/'adaptive-recording';file=folder/'settings.json'
        with self.storage_lock:
            if not file.is_file():raise ValueError('智能录像程序尚未安装')
            config=json.loads(file.read_text(encoding='utf8'));previous=bool(config['enabled']);config['enabled']=enabled
            if enabled and not previous:
                activation=folder/'activation.web.tmp';activation.write_text(json.dumps({'since':dt.datetime.now().isoformat()}),encoding='utf8');activation.replace(folder/'activation.json')
            pending=folder/'settings.web.tmp';pending.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf8');pending.replace(file)
        return self.adaptive_recording()

    @contextlib.contextmanager
    def db(self):
        connection=sqlite3.connect(self.source.as_uri()+'?mode=ro',uri=True,timeout=3)
        connection.row_factory=sqlite3.Row
        try:yield connection
        finally:connection.close()

    def event(self,row,cleared=None,name=None):
        value={key:text(row[key]) for key in ('event_id','channel_id','name','start_time','duration','snapshot_path','event_type')}
        value['name']=name or value['name'] or value['channel_id']
        try:self.snapshot(value);value['has_image']=True
        except (ValueError,OSError):value['has_image']=False
        value['video_cleared']=(value['event_id'],value['channel_id'],value['start_time']) in (self.cleared() if cleared is None else cleared)
        return value

    def payload(self):
        events=[];cameras=[];cleared=self.cleared()
        with self.db() as db:
            for camera in self.cameras():
                channel_id=camera['channel_id'];fallback_name=camera['name']
                name=fallback_name
                total=db.execute('select count(*) from events where channel_id=?',(channel_id,)).fetchone()[0]
                rows=db.execute('select e.id event_id,e.channel_id,c.name,e.start_time,e.duration,e.snapshot_path,e.event_type from events e left join channels c on c.id=e.channel_id where e.channel_id=? order by e.start_time desc,e.id desc limit 500',(channel_id,)).fetchall()
                events.extend(self.event(row,cleared,name) for row in rows)
                cameras.append({**camera,'name':name,'total':total,'shown':len(rows)})
        return {'events':events,'cameras':cameras}

    def row(self,event_id):
        with self.db() as db:
            row=db.execute('select e.id event_id,e.channel_id,c.name,e.start_time,e.duration,e.snapshot_path,e.event_type from events e left join channels c on c.id=e.channel_id where e.id=?',(int(event_id),)).fetchone()
        if not row:raise ValueError('没有找到这个事件')
        camera=next((c for c in self.cameras() if c['channel_id']==row['channel_id']),None)
        if not camera:raise ValueError('未知事件通道')
        return self.event(row,name=camera['name'])

    def snapshot(self,row):
        image=(self.records/str(row['snapshot_path']).replace('\\','/')).resolve()
        if not image.is_relative_to(self.records) or image.suffix.lower() not in ('.jpg','.jpeg','.png'):raise ValueError('截图路径无效')
        if not image.is_file():raise ValueError('事件截图已不存在')
        if image.stat().st_size>12*1024*1024:raise ValueError('事件截图过大')
        return image

    def thumbnail(self,row):
        source=self.snapshot(row);destination=self.cache/f'{row["event_id"]}-preview.jpg'
        with self.image_lock:
            if destination.exists() and destination.stat().st_mtime>=source.stat().st_mtime:return destination
            # Some EasyNVR .jpg files contain an encoded video keyframe, so decode locally.
            temporary=destination.with_name(destination.stem+'.tmp.jpg')
            command=[str(FFMPEG),'-hide_banner','-loglevel','error','-y','-i',str(source),'-frames:v','1','-vf','scale=960:-2','-q:v','4',str(temporary)]
            result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,creationflags=0x08000000)
            if result.returncode!=0 or not temporary.exists():raise ValueError('无法解码这次事件的截图')
            os.replace(temporary,destination)
        return destination

    def clips(self,row,index=None):
        start=dt.datetime.fromisoformat(row['start_time']).replace(tzinfo=None)
        end=start+dt.timedelta(milliseconds=max(1000,int(row['duration'] or 1000)))
        index={} if index is None else index
        cid=row['channel_id']
        if cid not in index:index[cid]=self.recording_root(cid)[1]
        root=index[cid]
        clips=[]
        day=start.date()-dt.timedelta(days=1)
        while day<=end.date():
            directory=root/day.strftime('%Y%m%d')
            if directory not in index:
                segments=[]
                if ordinary(directory):
                    for file in directory.glob('*.mp4'):
                        match=re.fullmatch(r'(\d{14})-([1-9]\d*)\.mp4',file.name)
                        if not match or not ordinary(file) or not file.is_file():continue
                        try:
                            if not file.stat().st_size:continue
                            clip_start=dt.datetime.strptime(match[1],'%Y%m%d%H%M%S')
                            duration=int(match[2])/1000
                            clip_end=clip_start+dt.timedelta(seconds=duration)
                        except (FileNotFoundError,ValueError,OverflowError):continue
                        segments.append((clip_start,clip_end,{'path':file,'start':clip_start,'duration':duration}))
                index[directory]=segments
            clips.extend(clip for clip_start,clip_end,clip in index[directory] if clip_start<end and clip_end>start)
            day+=dt.timedelta(days=1)
        return sorted(clips,key=lambda item:(item['start'],str(item['path'])))

    def video_info(self,row):
        clips=self.clips(row)
        if not clips:raise ValueError('该事件录像已清理，已保留图片和时间' if row.get('video_cleared') else '未找到该事件的本地录像，可能尚未生成或已被清理')
        offset=max(0,(dt.datetime.fromisoformat(row['start_time']).replace(tzinfo=None)-clips[0]['start']).total_seconds())
        return {'url':f'/api/events/{row["event_id"]}/video','parts':len(clips),'duration':sum(item['duration'] for item in clips),'start_time':clips[0]['start'].isoformat(' '),'offset':offset}

    def video(self,row):
        clips=self.clips(row)
        if not clips:raise ValueError('未找到该事件的本地录像')
        if len(clips)==1:return clips[0]['path']
        signature=hashlib.sha256('|'.join(str(item['path'])+':'+str(item['path'].stat().st_mtime_ns) for item in clips).encode()).hexdigest()[:16]
        output=self.cache/f'{row["event_id"]}-video-{signature}.mp4'
        with self.video_lock:
            if output.exists():return output
            manifest=output.with_suffix('.txt');temporary=output.with_suffix('.tmp.mp4')
            manifest.write_text(''.join("file '"+str(item['path']).replace('\\','/').replace("'","'\\''")+"'\n" for item in clips),encoding='utf8')
            try:
                command=[str(FFMPEG),'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(manifest),'-map','0:v:0','-map','0:a:0?','-c','copy','-movflags','+faststart',str(temporary)]
                result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,creationflags=0x08000000)
                if result.returncode!=0 or not temporary.exists():raise ValueError('事件录像暂时无法播放，请稍后重试')
                os.replace(temporary,output)
            finally:manifest.unlink(missing_ok=True);temporary.unlink(missing_ok=True)
        return output

    @contextlib.contextmanager
    def event_video(self,row):
        with self.video_lock:
            file=self.video(row)
            cached=file.parent==self.cache
            if cached:
                self.active_cached_videos[file]+=1
        try:
            yield file
        finally:
            if cached:
                with self.video_lock:
                    self.active_cached_videos[file]-=1
                    if not self.active_cached_videos[file]:
                        del self.active_cached_videos[file]

def handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value,kind='application/json; charset=utf-8'):
            data=json.dumps(value,ensure_ascii=False).encode() if kind.startswith('application/json') else value
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cross-Origin-Resource-Policy','same-origin');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'");self.end_headers()
            if not getattr(self,'headers_only',False):self.wfile.write(data)
        def send_video(self,file,download_name=None):
            with file.open('rb') as stream:
                self.send_video_stream(stream,download_name)
        def send_video_stream(self,stream,download_name):
            size=os.fstat(stream.fileno()).st_size;start=0;end=size-1;partial=False
            if self.headers.get('Range'):
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers['Range'])
                if not match or not any(match.groups()):
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.end_headers();return
                if match[1]:start=int(match[1]);end=min(int(match[2]),size-1) if match[2] else size-1
                else:start=max(0,size-int(match[2]))
                if start>=size or start>end:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.end_headers();return
                partial=True
            self.send_response(206 if partial else 200);self.send_header('Content-Type','video/mp4');self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-start+1));self.send_header('Cache-Control','private, max-age=60');self.send_header('Cross-Origin-Resource-Policy','same-origin')
            if download_name:self.send_header('Content-Disposition',"attachment; filename=event.mp4; filename*=UTF-8''"+quote(download_name,safe=''))
            if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if getattr(self,'headers_only',False):return
            try:
                stream.seek(start);remaining=end-start+1
                while remaining:
                    block=stream.read(min(65536,remaining))
                    if not block:break
                    self.wfile.write(block);remaining-=len(block)
            except (BrokenPipeError,ConnectionResetError):pass
        def allowed(self):return self.headers.get('Host') in (f'127.0.0.1:{PORT}',f'localhost:{PORT}')
        def do_GET(self):
            if not self.allowed():self.reply(403,{'error':'只允许本机访问'});return
            route=self.path.split('?')[0]
            try:
                if route in ('/api/daily/bootstrap','/api/daily/job'):self.reply(200,app.daily().get(route,parse_qs(urlsplit(self.path).query)))
                elif route.startswith('/api/daily/image/'):self.reply(200,app.daily().image(route[len('/api/daily/image/'):]).read_bytes(),'image/jpeg')
                elif route in ('/daily','/daily/','/daily/app.js','/daily/style.css'):
                    file=ROOT.parent/'daily-analysis'/('app.js' if route.endswith('app.js') else 'style.css' if route.endswith('style.css') else 'index.html');mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[file.suffix];self.reply(200,file.read_bytes(),mime)
                elif route=='/api/events':self.reply(200,app.payload())
                elif route=='/api/live':self.reply(200,channel_inventory(app.source,app.records))
                elif match:=re.fullmatch(r'/api/live/([A-Za-z0-9_.-]+)/(live\.m3u8|(?:segment|part)\d+\.ts)',route):
                    status,data,mime=relay_media(app.source,app.records,*match.groups());self.reply(status,data,mime)
                elif route=='/api/storage':self.reply(200,app.storage())
                elif route=='/api/recordings':
                    query=parse_qs(urlsplit(self.path).query);self.reply(200,app.recordings(query.get('channel_id',[''])[0],query.get('date',[None])[0]))
                elif re.fullmatch(r'/api/recordings/[A-Za-z0-9_.-]+/\d{14}-[1-9]\d*\.mp4/(?:video|download)',route):
                    _,_,_,channel,name,action=route.split('/');file=app.recording_file(channel,name)
                    self.send_video(file,(app.recording_root(channel)[0]['name']+'_'+name) if action=='download' else None)
                elif route=='/api/adaptive-recording':self.reply(200,app.adaptive_recording())
                elif route=='/api/health':self.reply(200,{'ok':True,'mode':'event_preview','source_available':app.source.exists()})
                elif re.fullmatch(r'/api/events/\d+/image',route):
                    row=app.row(int(route.split('/')[3]))
                    with app.image_lock:
                        data=app.thumbnail(row).read_bytes()
                    self.reply(200,data,'image/jpeg')
                elif re.fullmatch(r'/api/events/\d+/video-info',route):self.reply(200,app.video_info(app.row(int(route.split('/')[3]))))
                elif re.fullmatch(r'/api/events/\d+/video',route):
                    row=app.row(int(route.split('/')[3]))
                    with app.event_video(row) as file:
                        self.send_video(file)
                elif re.fullmatch(r'/api/events/\d+/download',route):
                    row=app.row(int(route.split('/')[3]));name=row['name']+'_'+dt.datetime.fromisoformat(row['start_time']).strftime('%Y%m%d_%H%M%S')+'.mp4'
                    with app.event_video(row) as file:
                        self.send_video(file,name)
                elif route in ('/live','/live/'):
                    self.reply(200,(ROOT/'live.html').read_bytes(),'text/html; charset=utf-8')
                elif route=='/replay-sync.json':
                    self.reply(200,json.loads((ROOT/'replay-sync.json').read_text(encoding='utf-8')))
                elif route in ('/','/app.js','/style.css','/replay.js','/replay.css','/artplayer.js','/theme.js','/live.js','/live.css','/hls.light.min.js'):
                    file=ROOT/('index.html' if route=='/' else route[1:]);mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[file.suffix];self.reply(200,file.read_bytes(),mime)
                else:self.reply(404,{'error':'未找到页面'})
            except ValueError as error:self.reply(404,{'error':str(error)})
            except (OSError,subprocess.TimeoutExpired):self.reply(404,{'error':'图片或事件录像暂时不可用'})
            except sqlite3.Error:self.reply(503,{'error':'暂时无法读取 EasyNVR 事件，请稍后重试'})
        def do_POST(self):
            route=self.path.split('?')[0]
            if route not in ('/api/storage/prepare-cleanup','/api/storage/cleanup','/api/storage/clear-preview-cache','/api/adaptive-recording','/api/daily/settings','/api/daily/create','/api/daily/cancel','/api/daily/test'):self.reply(405,{'error':'不支持此操作'});return
            if not self.allowed() or self.headers.get('Origin') not in (f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}') or self.headers.get('X-CSRF-Token')!=app.csrf:
                self.reply(403,{'error':'请从本机事件预览页面操作'});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=(32768 if route.startswith('/api/daily/') else 4096) or self.headers.get('Content-Type')!='application/json':raise ValueError('请求格式无效')
                body=json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('请求格式无效')
                if route.startswith('/api/daily/'):result=app.daily().post(route,body)
                elif route=='/api/adaptive-recording':result=app.set_adaptive_recording(body.get('enabled'))
                elif route=='/api/storage/clear-preview-cache':result=app.clear_preview_cache()
                elif route.endswith('prepare-cleanup'):result=app.prepare_cleanup(body.get('channel_id'),body.get('mode','all'))
                else:result=app.cleanup(str(body.get('token','')),body.get('channel_id'),body.get('confirmation'))
                self.reply(200,result)
            except (ValueError,TypeError) as error:self.reply(400,{'error':str(error)})
            except (OSError,sqlite3.Error):self.reply(503,{'error':'清理未完成，部分文件可能已删除；请刷新后重试'})
        def do_HEAD(self):self.headers_only=True;self.do_GET()
        def do_PUT(self):self.reply(405,{'error':'不支持此操作'})
        do_DELETE=do_PUT
    return Handler

if __name__=='__main__':
    app=App();app.daily();server=ThreadingHTTPServer(('127.0.0.1',PORT),handler(app))
    retention_dir=ROOT.parent/'recording-retention'
    if (retention_dir/'retention.py').is_file():
        sys.path.insert(0,str(retention_dir))
        from retention import start as start_retention
        app.retention=start_retention(app)
    (ROOT/'process.json').write_text(json.dumps({'pid':os.getpid(),'url':f'http://127.0.0.1:{PORT}','mode':'event_preview'}))
    try:server.serve_forever()
    finally:server.server_close()
