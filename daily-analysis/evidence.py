"""Read existing recordings and extract small, time-labelled JPEG evidence."""
import datetime as dt,hashlib,json,subprocess

def candidates(rows,locations):
    groups=[]
    for row in sorted(rows,key=lambda r:(r['channel_id'],r['start_time'])):
        start=dt.datetime.fromisoformat(row['start_time']).replace(tzinfo=None)
        end=start+dt.timedelta(seconds=max(1,int(row['duration'] or 1000)/1000))
        if groups and groups[-1]['channel_id']==row['channel_id'] and start<=groups[-1]['end']+dt.timedelta(seconds=15):
            groups[-1]['end']=max(end,groups[-1]['end']);groups[-1]['event_ids'].append(row['id']);groups[-1]['hits'].append((start,end))
        else:groups.append({'channel_id':row['channel_id'],'start':start,'end':end,'event_ids':[row['id']],'hits':[(start,end)]})
    segments=[]
    for group in groups:
        start=group['start']-dt.timedelta(seconds=5);end=group['end']+dt.timedelta(seconds=10)
        while start<end:
            stop=min(start+dt.timedelta(seconds=120),end)
            segments.append({**group,'start':start,'end':stop,'location':locations.get(group['channel_id'],'')})
            start=stop
    return sorted(segments,key=lambda s:(s['start'],s['channel_id']))

def index(app,channel,start,end):
    result=[];day=start.date()
    while day<=end.date():
        for item in app.recordings(channel,day.isoformat())['items']:
            t=dt.datetime.fromisoformat(item['start_time']);stop=t+dt.timedelta(seconds=item['duration'])
            if stop>start and t<end:
                file=app.recording_file(channel,item['name'])
                result.append({'start':t,'end':stop,'path':file,'size':file.stat().st_size,'mtime':file.stat().st_mtime_ns})
        day+=dt.timedelta(days=1)
    return sorted(result,key=lambda r:r['start'])

def fingerprint(segment,clips):
    data={k:(v.isoformat() if isinstance(v,dt.datetime) else v) for k,v in segment.items() if k!='hits'}
    data['hits']=[(a.isoformat(),b.isoformat()) for a,b in segment.get('hits',[])]
    data['files']=[(str(c['path']),c['size'],c['mtime']) for c in clips];data['version']='deepseek-flash-person-v1'
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

def extract(segment,clips,folder,ffmpeg,count=8,times=None):
    folder.mkdir(parents=True,exist_ok=True);length=(segment['end']-segment['start']).total_seconds()
    if times is None:
        # Include actual event instants: uniform padding samples alone can miss a 2-second person event.
        hits=[(max(a,segment['start']),min(b,segment['end'])) for a,b in segment.get('hits',[]) if a<segment['end'] and b>segment['start']]
        anchors=[]
        for a,b in hits:anchors.extend([a,a+(b-a)/2,b-dt.timedelta(milliseconds=100)])
        if len(anchors)>count-2:anchors=[anchors[round(i*(len(anchors)-1)/(count-3))] for i in range(count-2)]
        times=sorted(set(anchors+[segment['start'],segment['end']-dt.timedelta(milliseconds=100)]))
        uniform=[segment['start']+dt.timedelta(seconds=length*i/(count-1)) for i in range(count)]
        for t in uniform:
            if len(times)>=count:break
            if all(abs((t-old).total_seconds())>.15 for old in times):times.append(t)
        times.sort()
    frames=[]
    for i,t in enumerate(times):
        clip=next((c for c in clips if c['start']<=t<c['end']),None)
        if clip is None:continue
        path=folder/f'{i:03}.jpg'
        if not path.is_file():
            command=[str(ffmpeg),'-hide_banner','-loglevel','error','-y','-ss',str((t-clip['start']).total_seconds()),'-i',str(clip['path']),'-frames:v','1','-vf','scale=1280:720:force_original_aspect_ratio=decrease','-q:v','3',str(path)]
            try:
                done=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,creationflags=0x08000000)
                if done.returncode!=0:path.unlink(missing_ok=True);continue
            except subprocess.TimeoutExpired:path.unlink(missing_ok=True);continue
        if path.is_file() and path.stat().st_size:frames.append({'time':t.isoformat(' ',timespec='seconds'),'path':path})
    if len(frames)<2:raise ValueError('可用录像不足，无法提取连续图片；可能已清理或尚未录制完成')
    return frames
