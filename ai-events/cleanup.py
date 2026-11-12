"""Camera-scoped/global cleanup. Never accept filesystem paths from a browser."""
import contextlib,datetime as dt,pathlib,re,secrets,sqlite3,stat,time
from camera_registry import channel_lock,ordinary

class CleanupMixin:
    def clear_preview_cache(self):
        removed=released=skipped=0
        with self.video_lock,self.image_lock:
            cache=self.cache.resolve()
            if not ordinary(self.cache) or cache!=self.root.resolve()/'cache' or cache.is_relative_to(self.records) or self.records.is_relative_to(cache):
                raise ValueError('预览缓存目录无效')
            for file in self.cache.iterdir():
                if not re.fullmatch(r'\d+-(?:preview(?:\.tmp)?\.jpg|video-[a-f0-9]{16}(?:(?:\.tmp)?\.mp4|\.txt))',file.name):
                    continue
                if file in self.active_cached_videos:
                    skipped+=1
                    continue
                try:
                    if not ordinary(file):
                        continue
                    info=file.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    file.unlink()
                    removed+=1
                    released+=info.st_size
                except FileNotFoundError:
                    continue
                except OSError:
                    skipped+=1
        return {'removed':removed,'released':released,'skipped':skipped}

    def cleared(self):
        path=self.root/'preview-state.db'
        if not path.exists():return set()
        with contextlib.closing(sqlite3.connect(path,timeout=3)) as db:
            return {(r[0],r[1],r[2]) for r in db.execute('select event_id,channel_id,start_time from video_cleared')}

    def event_rows(self,db,channel_id):
        return [dict(row) for row in db.execute('select id,channel_id,start_time,duration,snapshot_path from events where channel_id=?',(channel_id,))]

    def event_recordings(self,rows):
        index={};paths=set();playable=[]
        for row in rows:
            clips=self.clips(row,index)
            if clips:
                paths.update(clip['path'] for clip in clips)
                playable.append((row['id'],row['channel_id'],row['start_time']))
        return paths,playable

    def recording_index_path(self,value):
        path=pathlib.Path(value)
        if not path.is_absolute():
            path=(self.records.parent if path.parts[:1]==(self.records.name,) else self.records)/path
        return path.resolve()

    def recent_recording(self,path,cutoff):
        match=re.fullmatch(r'(\d{14})-([1-9]\d*)\.mp4',path.name)
        if not match:return False
        try:
            start=dt.datetime.strptime(match[1],'%Y%m%d%H%M%S')
            return start+dt.timedelta(milliseconds=int(match[2]))>=cutoff
        except (ValueError,OverflowError):return True

    def prepare_cleanup(self,channel_id,mode='all'):
        if mode not in ('all','videos'):raise ValueError('未知清理方式')
        with self.storage_lock:
            inventory=self.cameras();selected=inventory if channel_id=='all' else [r for r in inventory if r['channel_id']==channel_id]
            if not selected:raise ValueError('未找到可清理通道')
            files=[];event_rows=[];recordings=[]
            with self.db() as db:
                record_columns={row['name'] for row in db.execute('pragma table_info(record_files)')}
                for camera in selected:
                    cid=camera['channel_id']
                    for f in self.camera_files(cid):
                        if mode=='all' or pathlib.Path(f['path']).suffix.lower()=='.mp4':files.append({**f,'channel_id':cid})
                    event_rows.extend(self.event_rows(db,cid))
                    if mode=='all' and record_columns:
                        recordings.extend((r[0],cid,None) for r in db.execute('select id from record_files where channel_id=?',(cid,)))
                    elif 'path' in record_columns:
                        recordings.extend((r[0],cid,r[1]) for r in db.execute('select id,path from record_files where channel_id=?',(cid,)) if r[1])
            protected,playable=self.event_recordings(event_rows) if mode=='videos' else (set(),[])
            if mode=='videos':
                cutoff=dt.datetime.now()-dt.timedelta(minutes=2)
                files=[file for file in files if pathlib.Path(file['path']) not in protected and not self.recent_recording(pathlib.Path(file['path']),cutoff)]
            ids={r['id']:r['channel_id'] for r in event_rows}
            if mode=='all':
                for file in self.cache.iterdir():
                    match=re.match(r'^(\d+)-(?:preview|video-)',file.name)
                    if match and int(match[1]) in ids and file.is_file() and ordinary(file):
                        info=file.stat();files.append({'path':str(file),'size':info.st_size,'mtime':info.st_mtime_ns,'channel_id':ids[int(match[1])]})
            samples=self.root.parent/'adaptive-recording'/'last-original'
            if mode=='all' and samples.exists() and ordinary(samples):
                for camera in selected:
                    for suffix in ('.mp4','.json'):
                        file=samples/(camera['channel_id']+suffix)
                        if file.is_file() and ordinary(file):
                            info=file.stat();files.append({'path':str(file),'size':info.st_size,'mtime':info.st_mtime_ns,'channel_id':camera['channel_id']})
            now=time.monotonic();self.cleanup_plans={k:v for k,v in self.cleanup_plans.items() if now-v['at']<300}
            if len(self.cleanup_plans)>=20:raise ValueError('待确认操作过多，请稍后重试')
            token=secrets.token_urlsafe(32);name=f'全部{len(selected)}路摄像头' if channel_id=='all' else selected[0]['name']
            summary={'token':token,'channel_id':channel_id,'mode':mode,'name':name,'camera_count':len(selected),'cameras':[{'channel_id':r['channel_id'],'name':r['name'],'archived':r['archived']} for r in selected],'events':len(event_rows),'files':len(files),'bytes':sum(f['size'] for f in files),'protected_videos':len(protected),'playable_events':len(playable)}
            fingerprint=sorted((r['channel_id'],r['name'],r['archived']) for r in inventory)
            self.cleanup_plans[token]={'at':now,'summary':summary,'files':files,'events':[r['id'] for r in event_rows],'event_rows':event_rows,'recordings':recordings,'fingerprint':fingerprint}
            return summary

    def cleanup(self,token,channel_id,confirmation):
        with self.storage_lock,self.video_lock,self.image_lock:
            plan=self.cleanup_plans.get(token)
            if not plan or time.monotonic()-plan['at']>300:raise ValueError('确认已过期，请重新选择清空操作')
            summary=plan['summary']
            if channel_id!=summary['channel_id'] or confirmation!=summary['name']:self.cleanup_plans.pop(token,None);raise ValueError('摄像头名称确认不匹配')
            inventory=self.cameras();fingerprint=sorted((r['channel_id'],r['name'],r['archived']) for r in inventory)
            if fingerprint!=plan['fingerprint']:self.cleanup_plans.pop(token,None);raise ValueError('通道配置已变化，请重新确认清理范围')
            selected=[r['channel_id'] for r in summary['cameras']];mode=summary['mode'];removed=0;released=0;skipped=0;skipped_channels=set()
            with contextlib.ExitStack() as stack:
                for cid in sorted(selected):stack.enter_context(channel_lock(cid,self.root.parent/'channel-locks',timeout=3))
                self.cleanup_plans.pop(token,None)
                allowed={f['path'] for cid in selected for f in self.camera_files(cid)}
                protected=set();playable=[];event_rows=plan['event_rows'];removed_paths=set()
                if mode=='videos':
                    with self.db() as db:
                        event_rows=[row for cid in selected for row in self.event_rows(db,cid)]
                    protected,playable=self.event_recordings(event_rows)
                sample_root=self.root.parent/'adaptive-recording'/'last-original'
                for item in plan['files']:
                    file=pathlib.Path(item['path']);cid=item.get('channel_id',selected[0])
                    if file in protected:continue
                    cache_ok=file.parent==self.cache and re.match(r'^\d+-(?:preview|video-)',file.name)
                    sample_ok=file.parent==sample_root and file.name in (cid+'.mp4',cid+'.json') and ordinary(sample_root)
                    if not file.exists():continue
                    try:
                        if not ordinary(file) or (not cache_ok and not sample_ok and str(file) not in allowed):raise OSError('File no longer allowed')
                        info=file.stat()
                        if (info.st_size,info.st_mtime_ns)!=(item['size'],item['mtime']):raise OSError('File changed')
                        file.unlink();removed+=1;released+=info.st_size;removed_paths.add(file)
                    except OSError:skipped+=1;skipped_channels.add(cid)
                deleted_events=0;deleted_keys=set()
                with contextlib.closing(sqlite3.connect(self.source,timeout=5)) as db:
                    with db:
                        if mode=='all':
                            for row in plan['event_rows']:
                                keep=False
                                if row['channel_id'] in skipped_channels:
                                    try:self.snapshot(row);keep=True
                                    except (ValueError,OSError):pass
                                if not keep:
                                    count=db.execute('delete from events where id=? and channel_id=? and start_time=? and duration is ?',(row['id'],row['channel_id'],row['start_time'],row['duration'])).rowcount
                                    deleted_events+=count
                                    if count:deleted_keys.add((row['id'],row['channel_id'],row['start_time']))
                        for record_id,cid,path in plan['recordings']:
                            if mode=='all' and cid not in skipped_channels:
                                db.execute('delete from record_files where id=? and channel_id=?',(record_id,cid))
                            elif mode=='videos' and self.recording_index_path(path) in removed_paths:
                                db.execute('delete from record_files where id=? and channel_id=? and path=?',(record_id,cid,path))
                with contextlib.closing(sqlite3.connect(self.root/'preview-state.db',timeout=5)) as state:
                    with state:
                        state.execute('create table if not exists video_cleared(event_id integer,channel_id text,start_time text,cleared_at text,primary key(event_id,channel_id,start_time))')
                        state.executemany('delete from video_cleared where event_id=? and channel_id=? and start_time=?',deleted_keys if mode=='all' else playable)
            self.storage_cache=None
            return {'removed':removed,'released':released,'skipped':skipped,'events':deleted_events,'kept_events':len(event_rows) if mode=='videos' else 0,'protected_videos':len(protected),'playable_events':len(playable),'mode':mode,'complete':not skipped}
