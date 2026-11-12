"""Optimize only newly completed recordings; keep timestamp-based event playback intact."""
import sys
import contextlib,datetime as dt,hashlib,json,os,pathlib,re,shutil,sqlite3,stat,subprocess,threading,time
import numpy as np

ROOT=pathlib.Path(__file__).resolve().parent
RECORDS=pathlib.Path(r'C:\EasyNVR\r\easynvr_rec')
SOURCE_DB=pathlib.Path(r'C:\EasyNVR\configs\data.db')
FFMPEG=pathlib.Path(r'C:\Users\Administrator\AppData\Local\Programs\Open Design\resources\app\node_modules\@ffmpeg-installer\win32-x64\ffmpeg.exe')
sys.path.insert(0,str(ROOT.parent))
from camera_registry import Registry,channel_lock
PATTERN=re.compile(r'^(\d{14})-(\d+)\.mp4$')

def run(arguments,timeout=120):
    return subprocess.run([str(FFMPEG),'-nostdin','-hide_banner',*map(str,arguments)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)

def probe(file):
    reply=run(['-i',file],30);text=reply.stderr.decode('utf8','replace')
    match=re.search(r'Duration: (\d+):(\d+):([\d.]+)',text)
    if not match or 'Video: h264' not in text:raise ValueError('Expected finalized H.264 video')
    duration=int(match[1])*3600+int(match[2])*60+float(match[3]);video=next(s for s in text.splitlines() if 'Video:' in s)
    size=re.search(r'\b(\d{2,5})x(\d{2,5})\b',video)
    return {'duration':duration,'width':int(size[1]) if size else None,'height':int(size[2]) if size else None,'audio':bool(re.search(r'Stream .*Audio:',text))}

def merge(windows,duration):
    result=[]
    for start,end in sorted((max(0.,a),min(duration,b)) for a,b in windows if b>0 and a<duration):
        if end<=start:continue
        if result and start<=result[-1][1]+.25:result[-1][1]=max(result[-1][1],end)
        else:result.append([start,end])
    return result

def motion_windows(file,duration,settings):
    fps=settings['analysis_fps'];reply=run(['-v','error','-i',file,'-map','0:v:0','-an','-vf',f'fps={fps},scale=160:90','-pix_fmt','gray','-f','rawvideo','pipe:1'])
    if reply.returncode:raise RuntimeError('Motion analysis failed')
    if len(reply.stdout)%14400:raise ValueError('Incomplete analysis frame')
    frames=np.frombuffer(reply.stdout,dtype=np.uint8).reshape(-1,90,160)
    if len(frames)<2:raise ValueError('Too few analysis frames')
    windows=[];previous=frames[0].astype(np.int16)
    for i,frame in enumerate(frames[1:],1):
        current=frame.astype(np.int16);difference=current-previous
        # Ignore global exposure shifts; evaluate changes in small spatial blocks.
        difference=np.abs(difference-np.median(difference))
        changed=difference>=settings['motion_pixel_difference']
        # Require adjacent changed pixels to reduce isolated sensor noise.
        supported=changed[1:-1,1:-1] & (changed[:-2,1:-1]|changed[2:,1:-1]|changed[1:-1,:-2]|changed[1:-1,2:])
        if float(supported.mean())>=settings['motion_area_ratio']:
            moment=i/fps;windows.append((moment-settings['pre_seconds'],moment+settings['post_seconds']))
        previous=current
    return merge(windows,duration+settings['post_seconds'])

def event_windows(channel,start,duration,settings,source=SOURCE_DB):
    windows=[]
    with contextlib.closing(sqlite3.connect(pathlib.Path(source).as_uri()+'?mode=ro',uri=True,timeout=3)) as db:
        # Named-camera identity and actual parsed time avoid timezone-string ordering errors.
        day=(start-dt.timedelta(days=1)).strftime('%Y-%m-%d')
        rows=db.execute('select start_time,duration from events where channel_id=? and substr(start_time,1,10)>=?',(channel,day))
        for stamp,milliseconds in rows:
            try:offset=(dt.datetime.fromisoformat(stamp).replace(tzinfo=None)-start).total_seconds()
            except (ValueError,TypeError):continue
            windows.append((offset-settings['pre_seconds'],offset+max(1,int(milliseconds or 1000))/1000+settings['post_seconds']))
    return merge(windows,duration+settings['post_seconds'])

def select_filter(windows,duration,settings):
    # Preserve both sides of file boundaries, plus precise motion/event windows.
    windows=merge([*windows,(0,settings['pre_seconds']),(duration-settings['pre_seconds'],duration)],duration)
    if len(windows)>80:windows=[[0,duration]]
    active='+'.join(f'between(t,{a:.3f},{b:.3f})' for a,b in windows) or '0'
    return f"select='if({active},1,isnan(prev_selected_t)+gte(t-prev_selected_t,{1/settings['idle_fps']:.6f}))'",windows

def signature(file):
    s=file.stat();return s.st_size,s.st_mtime_ns

def optimize(source,destination,windows,settings):
    before=probe(source);vf,windows=select_filter(windows,before['duration'],settings)
    reply=run(['-v','error','-y','-i',source,'-map','0:v:0','-map','0:a:0?','-vf',vf,'-vsync','vfr','-c:v','libx264','-preset',settings['preset'],'-crf',settings['crf'],'-maxrate',f"{settings['maxrate_kbps']}k",'-bufsize',f"{settings['maxrate_kbps']*2}k",'-pix_fmt','yuv420p','-g','120','-force_key_frames','expr:gte(t,n_forced*2)','-c:a','copy','-video_track_timescale','90000','-movflags','+faststart',destination])
    if reply.returncode:raise RuntimeError('Adaptive video encoding failed: '+reply.stderr.decode('utf8','replace')[-1000:])
    after=probe(destination)
    if (after['width'],after['height'],after['audio'])!=(before['width'],before['height'],before['audio']):raise ValueError('Video dimensions or audio changed')
    if abs(after['duration']-before['duration'])>.35:raise ValueError('Timeline duration changed')
    decoded=run(['-v','error','-i',destination,'-map','0:v:0','-f','null','-'])
    if decoded.returncode or decoded.stderr.strip():raise ValueError('Encoded video did not pass full decode')
    return before,after,windows

def atomic_json(file,data):
    temporary=file.with_name(file.name+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
    # Windows readers can briefly block a rename while the destination is open.
    for attempt in range(6):
        try:
            temporary.replace(file)
            return
        except OSError as error:
            if getattr(error,'winerror',None) not in (5,32,33) or attempt==5:
                raise
            time.sleep(.05 * 2**attempt)

def publish_status(state):
    state['updated_at']=dt.datetime.now().isoformat()
    try:
        atomic_json(ROOT/'status.json',state)
    except OSError as error:
        # Status is advisory. Leave the last valid snapshot in place and try again later.
        print(f"{state['updated_at']} Unable to publish status: {error}",file=sys.stderr,flush=True)

def pending_recordings(base,records,since,settle_seconds,jobs):
    pending=[]
    for file in base.glob('*/*.mp4'):
        match=PATTERN.fullmatch(file.name)
        if not match or not 0<int(match[2])<=180000:
            continue
        try:
            start=dt.datetime.strptime(match[1],'%Y%m%d%H%M%S')
        except ValueError:
            continue
        if start<since:
            continue
        try:
            info=file.lstat()
            if not stat.S_ISREG(info.st_mode) or getattr(info,'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT:
                continue
            if time.time()-info.st_mtime<settle_seconds or not file.resolve().is_relative_to(records):
                continue
        except OSError:
            # Recording cleanup or a Windows lock can invalidate a path during this scan.
            # Leave the file eligible for a later scan and keep processing the other files.
            continue
        if jobs.execute('select 1 from jobs where path=?',(str(file),)).fetchone():
            continue
        pending.append((start,file))
    return pending

def main():
    # Named mutex prevents duplicate workers from competing for the same file.
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.restype=ctypes.c_void_p
    handle=kernel.CreateMutexW(None,False,'Local\\CameraMonitorAdaptiveRecording')
    if ctypes.get_last_error()==183:return
    if not handle:raise OSError('Unable to create worker mutex')
    ROOT.mkdir(exist_ok=True);(ROOT/'work').mkdir(exist_ok=True);(ROOT/'last-original').mkdir(exist_ok=True)
    activation=ROOT/'activation.json'
    if not activation.exists():atomic_json(activation,{'since':dt.datetime.now().isoformat()})
    since=dt.datetime.fromisoformat(json.loads(activation.read_text(encoding='utf8'))['since'])
    atomic_json(ROOT/'process.json',{'pid':os.getpid()})
    jobs=sqlite3.connect(ROOT/'jobs.db');jobs.execute('create table if not exists jobs(path text primary key,channel text,original_bytes integer,stored_bytes integer,motion_seconds real,duration real,finished_at text)');jobs.commit()
    state={'running':True,'since':since.isoformat(),'cameras':{},'current':None}
    while not (ROOT/'stop.request').exists():
        settings=json.loads((ROOT/'settings.json').read_text(encoding='utf8'));state['enabled']=bool(settings['enabled'])
        since=dt.datetime.fromisoformat(json.loads(activation.read_text(encoding='utf8'))['since']);state['since']=since.isoformat()
        if not 0<settings['idle_fps']<=5 or not 0<settings['analysis_fps']<=10:raise ValueError('Invalid sampling rates')
        if settings['enabled']:
            for camera in Registry(SOURCE_DB,RECORDS).list(history=False):
                if not camera['enabled']:continue
                channel=camera['channel_id']
                if (ROOT/'stop.request').exists() or not json.loads((ROOT/'settings.json').read_text(encoding='utf8'))['enabled']:break
                device,number=camera['device_id'],camera['number']
                base=RECORDS/device/number
                try:
                    pending=pending_recordings(base,RECORDS.resolve(),since,settings['settle_seconds'],jobs)
                except (OSError,sqlite3.Error) as error:
                    state['cameras'].setdefault(channel,{})['error']=str(error)
                    continue
                if not pending:continue
                start,file=min(pending);temporary=ROOT/'work'/(channel+'.mp4');state['current']={'channel':channel,'file':file.name};publish_status(state)
                try:
                    original=signature(file);info=probe(file)
                    windows=motion_windows(file,info['duration'],settings)+event_windows(channel,start,info['duration'],settings)
                    # Extend prior segment activity through this segment's start.
                    previous=state['cameras'].get(channel,{}).get('active_until')
                    if previous:windows.append((0,(dt.datetime.fromisoformat(previous)-start).total_seconds()))
                    activity_end=max((b for a,b in windows),default=0)
                    before,after,windows=optimize(file,temporary,merge(windows,info['duration']),settings)
                    with channel_lock(channel):
                        if signature(file)!=original:raise RuntimeError('Recording changed while optimizing')
                        stored=temporary.stat().st_size;replaced=False
                        if stored<original[0]*.98:
                            # Keep the latest full-rate source per camera as a bounded rollback sample.
                            backup=ROOT/'last-original'/(channel+'.mp4');backup_tmp=backup.with_suffix('.tmp')
                            shutil.copy2(file,backup_tmp);os.replace(backup_tmp,backup)
                            atomic_json(backup.with_suffix('.json'),{'source':str(file),'original_bytes':original[0],'at':dt.datetime.now().isoformat()})
                            if signature(file)!=original:raise RuntimeError('Recording changed before replacement')
                            os.replace(temporary,file);replaced=True
                        else:stored=original[0]
                        now=dt.datetime.now().isoformat();jobs.execute('insert into jobs values(?,?,?,?,?,?,?)',(str(file),channel,original[0],stored,sum(b-a for a,b in windows),info['duration'],now));jobs.commit()
                    # Motion holds are conservative at boundaries; records and time range stay intact.
                    active_until=(start+dt.timedelta(seconds=activity_end)).isoformat()
                    total=jobs.execute('select count(*),sum(original_bytes),sum(stored_bytes) from jobs where channel=?',(channel,)).fetchone()
                    state['cameras'][channel]={'last_file':file.name,'at':now,'original_bytes':original[0],'stored_bytes':stored,'saved_percent':round(100*(1-stored/original[0]),1),'optimized':replaced,'active_seconds':round(sum(b-a for a,b in windows),1),'duration':info['duration'],'files':total[0],'total_original_bytes':total[1],'total_stored_bytes':total[2],'active_until':active_until,'error':None}
                except (OSError,ValueError,RuntimeError,sqlite3.Error,subprocess.TimeoutExpired) as error:
                    state['cameras'].setdefault(channel,{})['error']=str(error)
                finally:
                    state['current']=None
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError as error:
                        state['cameras'].setdefault(channel,{})['error']=str(error)
        counts=jobs.execute('select count(*),coalesce(sum(original_bytes),0),coalesce(sum(stored_bytes),0) from jobs').fetchone()
        state['totals']={'files':counts[0],'original_bytes':counts[1],'stored_bytes':counts[2],'saved_bytes':counts[1]-counts[2]}
        publish_status(state);time.sleep(5)
    state['running']=False;publish_status(state);jobs.close()

if __name__=='__main__':main()
