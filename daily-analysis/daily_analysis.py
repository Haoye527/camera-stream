"""Independent daily analysis worker, called by the existing local event server."""
import base64,contextlib,ctypes,datetime as dt,json,os,pathlib,re,sqlite3,threading,time,uuid
from urllib.parse import urlencode
import evidence,vision

ZONE=dt.timezone(dt.timedelta(hours=8))
ROOT=pathlib.Path(__file__).resolve().parent

def now():return dt.datetime.now(ZONE)

def protect(value,decrypt=False):
    """Windows DPAPI: a copied bundle cannot disclose another Windows user's API key."""
    class Blob(ctypes.Structure):_fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value) if decrypt else value.encode()
    buffer=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):raise ValueError('本机密钥加密/解密失败，请重新填写 API Key')
    try:
        result=ctypes.string_at(target.data,target.size)
        return result.decode() if decrypt else base64.b64encode(result).decode('ascii')
    finally:ctypes.windll.kernel32.LocalFree(target.data)

class DailyService:
    def __init__(self,app,ffmpeg,root=ROOT,start=True):
        self.app=app;self.ffmpeg=ffmpeg;self.root=pathlib.Path(root);self.data=self.root/'data';self.data.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.wake=threading.Event();self.path=self.data/'analysis.db';self.configfile=self.data/'settings.private.json'
        with self.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS cache(id TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS calls(day TEXT PRIMARY KEY,count INTEGER);')
        # Interrupted work is resumed; completed segment results are cached.
        for job in self.jobs():
            if job['status']=='running':job['status']='queued';self.save(job)
        if start:threading.Thread(target=self.loop,daemon=True,name='daily-analysis').start()

    @contextlib.contextmanager
    def db(self):
        with contextlib.closing(sqlite3.connect(self.path,timeout=20)) as db:
            with db:yield db

    def config(self):
        default={'locations':{},'automatic':False,'daily_limit':160,'max_segments':60}
        if self.configfile.exists():default.update(json.loads(self.configfile.read_text(encoding='utf8')))
        return default

    def key(self):
        value=self.config().get('key_dpapi')
        return protect(value,True) if value else os.environ.get('DEEPSEEK_API_KEY','')

    def bootstrap(self):
        config=self.config();cameras=self.app.cameras()
        return {'csrf':self.app.csrf,'model':vision.MODEL,'base_url':vision.BASE,'has_key':bool(config.get('key_dpapi') or os.environ.get('DEEPSEEK_API_KEY')),'automatic':config['automatic'],'daily_limit':config['daily_limit'],'max_segments':config['max_segments'],'cameras':[{'id':c['channel_id'],'name':c['name'],'location':config['locations'].get(c['channel_id'],c['name'])} for c in cameras],'jobs':self.jobs(),'today':now().date().isoformat()}

    def settings(self,body):
        with self.lock:
            config=self.config();locations=body.get('locations',{})
            if not isinstance(locations,dict):raise ValueError('监控位置格式无效')
            known={c['channel_id'] for c in self.app.cameras()}
            if any(k not in known or not isinstance(v,str) or not 1<=len(v.strip())<=120 for k,v in locations.items()):raise ValueError('监控位置或通道无效')
            if type(body.get('automatic')) is not bool:raise ValueError('自动日报开关无效')
            limit=body.get('daily_limit');maximum=body.get('max_segments')
            if type(limit) is not int or not 2<=limit<=1000 or type(maximum) is not int or not 1<=maximum<=120:raise ValueError('调用上限为2～1000，单次片段数为1～120')
            config.update(locations={**config['locations'],**{k:v.strip() for k,v in locations.items()}},automatic=body['automatic'],daily_limit=limit,max_segments=maximum)
            key=body.get('api_key','')
            if not isinstance(key,str) or len(key)>512 or any(ord(c)<32 for c in key):raise ValueError('API Key 格式无效')
            if key.strip():config['key_dpapi']=protect(key.strip())
            if body.get('clear_key') is True:config.pop('key_dpapi',None)
            temporary=self.configfile.with_suffix('.tmp');temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temporary,self.configfile)
        return {'ok':True}

    def jobs(self):
        with self.db() as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM jobs ORDER BY rowid DESC LIMIT 100')]

    def job(self,id):
        with self.db() as db:row=db.execute('SELECT payload FROM jobs WHERE id=?',(id,)).fetchone()
        if not row:raise ValueError('没有找到日报任务')
        return json.loads(row[0])

    def save(self,job):
        job['updated']=now().isoformat(timespec='seconds')
        with self.db() as db:db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?)',(job['id'],json.dumps(job,ensure_ascii=False)))

    def create(self,body):
        day=body.get('date','');dt.date.fromisoformat(day)
        if len(day)!=10 or day>now().date().isoformat():raise ValueError('请选择今天或过去的日期')
        channel=body.get('channel_id','');known={c['channel_id'] for c in self.app.cameras()}
        if channel and channel not in known:raise ValueError('未知摄像头')
        if not self.key():raise ValueError('请先保存本机 DeepSeek API Key')
        with self.lock:
            pending=next((j for j in self.jobs() if j['status'] in ('queued','running') and j['date']==day and j['channel_id']==channel),None)
            if pending:return pending
            job={'id':uuid.uuid4().hex,'date':day,'channel_id':channel,'status':'queued','progress':0,'total':0,'results':[],'failures':[],'summary':None,'created':now().isoformat(timespec='seconds')}
            self.save(job);self.wake.set();return job

    def budget(self):
        with self.lock,self.db() as db:
            day=now().date().isoformat();row=db.execute('SELECT count FROM calls WHERE day=?',(day,)).fetchone();count=row[0] if row else 0
            if count>=self.config()['daily_limit']:raise ValueError('已达到今天的 API 调用上限，请调整上限或明天重试')
            db.execute('INSERT OR REPLACE INTO calls VALUES(?,?)',(day,count+1))

    def cancel(self,id):
        with self.lock:
            job=self.job(id)
            if job['status'] in ('queued','running'):job['status']='cancelled';self.save(job)
        return {'ok':True}

    def stopped(self,job):return self.job(job['id'])['status']=='cancelled'

    def publish(self,job):
        with self.lock:
            if self.stopped(job):return False
            self.save(job);return True

    def run(self,job):
        config=self.config();key=self.key();inventory={c['channel_id']:c for c in self.app.cameras()}
        selected={k:c for k,c in inventory.items() if not job['channel_id'] or k==job['channel_id']}
        locations={k:config['locations'].get(k,c['name']) for k,c in selected.items()}
        day=dt.date.fromisoformat(job['date']);start=dt.datetime.combine(day,dt.time());end=start+dt.timedelta(days=1)
        with self.app.db() as db:
            rows=[dict(r) for r in db.execute('SELECT id,channel_id,start_time,duration FROM events WHERE CAST(event_type AS TEXT)=? AND start_time<? AND datetime(start_time, printf("+%f seconds",duration/1000.0))>=? ORDER BY start_time',('1',end.isoformat(' '),start.isoformat(' '))) if r['channel_id'] in selected]
        segments=evidence.candidates(rows,locations)
        for s in segments:s['start']=max(start,s['start']);s['end']=min(end,s['end'])
        segments=[s for s in segments if s['end']>s['start']]
        job.update(status='running',total=min(len(segments),config['max_segments']),progress=0,results=[],failures=[],summary=None,available_segments=len(segments),limited=len(segments)>config['max_segments'],source_events=len(rows),model=vision.MODEL)
        self.publish(job)
        for segment in segments[:config['max_segments']]:
            if self.stopped(job):return
            label={'channel_id':segment['channel_id'],'location':segment['location'],'start':segment['start'].isoformat(' ',timespec='seconds'),'end':segment['end'].isoformat(' ',timespec='seconds'),'event_ids':segment['event_ids']}
            try:
                clips=evidence.index(self.app,segment['channel_id'],segment['start'],segment['end']);identity=evidence.fingerprint(segment,clips)
                with self.db() as db:cached=db.execute('SELECT payload FROM cache WHERE id=?',(identity,)).fetchone()
                if cached:
                    result=json.loads(cached[0])
                    # Clear operations may have removed an old evidence file.
                    if not all((self.data/'evidence'/f['file']).is_file() for f in result['frames']):cached=None
                if not cached:
                    folder=self.data/'evidence'/identity;frames=evidence.extract(segment,clips,folder,self.ffmpeg)
                    if self.stopped(job):return
                    analysis,usage=vision.analyze(key,segment['location'],frames,self.budget)
                    verification=False
                    if '疑似摔倒' in analysis['actions']:
                        centre=dt.datetime.fromisoformat(frames[analysis['evidence_frames'][0]-1]['time'])
                        times=[centre+dt.timedelta(seconds=i/2) for i in range(-6,7)]
                        dense=evidence.extract(segment,clips,folder/'verify',self.ffmpeg,times=times)
                        if self.stopped(job):return
                        analysis,extra=vision.analyze(key,segment['location'],dense,self.budget,True);frames=dense;verification=True
                        usage={k:usage.get(k,0)+extra.get(k,0) for k in ('prompt_tokens','completion_tokens','total_tokens')}
                    result={**label,**analysis,'id':identity,'usage':usage,'fall_rechecked':verification,'frames':[{'time':f['time'],'file':f['path'].relative_to(self.data/'evidence').as_posix()} for f in frames]}
                    with self.db() as db:db.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',(identity,json.dumps(result,ensure_ascii=False)))
                job['results'].append(result)
            except (ValueError,OSError,sqlite3.Error) as error:
                # Never include raw network responses, keys, or base64 evidence.
                message=str(error) if isinstance(error,ValueError) else '本机录像或分析存储暂时不可用'
                job['failures'].append({**label,'error':message})
            job['progress']+=1
            if not self.publish(job):return
        if self.stopped(job):return
        if job['results']:
            try:job['summary'],job['summary_usage']=vision.summarize(key,job['results'],self.budget)
            except ValueError as error:job['summary_error']=str(error)
        job['status']='partial' if job['failures'] or job.get('summary_error') or job['limited'] else 'complete'
        if not job['results'] and not segments:job['note']='这一天没有已记录的人形事件；不代表全天没有人物活动。'
        self.publish(job)

    def loop(self):
        while True:
            try:
                config=self.config();current=now()
                if config['automatic'] and (current.hour,current.minute)>=(0,10) and (config.get('key_dpapi') or os.environ.get('DEEPSEEK_API_KEY')):
                    day=(current.date()-dt.timedelta(days=1)).isoformat()
                    if not any(j['date']==day and not j['channel_id'] for j in self.jobs()):self.create({'date':day})
                pending=next((j for j in reversed(self.jobs()) if j['status']=='queued'),None)
                if pending:
                    try:self.run(pending)
                    except Exception:
                        pending['status']='failed';pending['error']='任务中断，请检查本机录像、密钥或服务状态后重试';self.publish(pending)
                    continue
            except Exception:pass # Retry storage/config access without taking down the event service.
            self.wake.wait(20);self.wake.clear()

    def image(self,name):
        if not re.fullmatch(r'[a-f0-9]{64}/(?:verify/)?\d{3}\.jpg',name):raise ValueError('证据图片地址无效')
        root=(self.data/'evidence').resolve();file=(root/name).resolve()
        if not file.is_relative_to(root) or not file.is_file():raise ValueError('证据图片已不存在')
        return file

    def get(self,route,query):
        if route=='/api/daily/bootstrap':return self.bootstrap()
        if route=='/api/daily/job':return self.job(query.get('id',[''])[0])
        raise ValueError('未知日报接口')

    def post(self,route,body):
        if route=='/api/daily/settings':return self.settings(body)
        if route=='/api/daily/create':return self.create(body)
        if route=='/api/daily/cancel':return self.cancel(body.get('id',''))
        if route=='/api/daily/test':
            result,_=vision.request(self.key(),[{'role':'user','content':'仅返回 JSON {"ok":true}'}],self.budget)
            if result.get('ok') is not True:raise ValueError('模型测试响应无效')
            return {'ok':True,'model':vision.MODEL,'note':'文本接口连接成功，图片能力将在实际事件分析时验证'}
        raise ValueError('未知日报接口')
