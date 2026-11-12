"""Shared, live EasyNVR channel inventory and cross-process maintenance locks."""
import contextlib,hashlib,json,os,pathlib,re,sqlite3,stat,threading,time

ROOT=pathlib.Path(__file__).resolve().parent
DEFAULT_DB=pathlib.Path(r'C:\EasyNVR\configs\data.db')
DEFAULT_RECORDS=pathlib.Path(r'C:\EasyNVR\r\easynvr_rec')
SAFE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,180}$')

def text(value):
    if isinstance(value,bytes):
        try:return value.decode('utf8')
        except UnicodeDecodeError:return value.decode('gb18030','replace')
    return value

def ordinary(path):
    try:s=path.lstat()
    except FileNotFoundError:return False
    return not path.is_symlink() and not (getattr(s,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',1024))

def has_media(folder):
    for directory,dirs,names in os.walk(folder,followlinks=False):
        dirs[:]=[name for name in dirs if ordinary(pathlib.Path(directory)/name)]
        if any(pathlib.Path(name).suffix.lower() in ('.mp4','.jpg','.jpeg','.png') and ordinary(pathlib.Path(directory)/name) for name in names):return True
    return False

def parts(channel_id,device_id=None):
    if not SAFE.fullmatch(str(channel_id)) or '..' in channel_id:raise ValueError('Invalid channel ID')
    if device_id and channel_id.startswith(device_id+'_'):number=channel_id[len(device_id)+1:]
    else:
        try:device_id,number=channel_id.rsplit('_',1)
        except ValueError:raise ValueError('Channel directory mapping unavailable')
    if not SAFE.fullmatch(device_id) or not SAFE.fullmatch(number) or '..' in device_id or '..' in number:raise ValueError('Invalid channel directory')
    return device_id,number

class Registry:
    def __init__(self,source=DEFAULT_DB,records=DEFAULT_RECORDS):self.source=pathlib.Path(source).resolve();self.records=pathlib.Path(records).resolve()
    def list(self,history=True,private=False):
        with contextlib.closing(sqlite3.connect(self.source.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            db.row_factory=sqlite3.Row;tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
            devices={str(r['id']):dict(r) for r in db.execute('select * from devices')} if 'devices' in tables else {}
            result={}
            for row in db.execute('select * from channels'):
                r=dict(row);channel=str(r['id'])
                try:device,number=parts(channel,text(r.get('device_id')))
                except ValueError:continue
                owner=devices.get(device,{})
                item={'channel_id':channel,'device_id':device,'number':number,'name':text(r.get('custom_name') or owner.get('custom_name') or owner.get('name') or r.get('name')) or channel,'enabled':bool(r.get('enabled',1)),'online':bool(r.get('status',0)),'archived':False}
                if private:item['source_url']=text(owner.get('url') or '');item['protocol']=text(r.get('protocol') or owner.get('protocol') or '')
                result[channel]=item
            if history:
                ids={str(r[0]) for r in db.execute('select distinct channel_id from events')} if 'events' in tables else set()
                if self.records.exists():
                    for parent in self.records.iterdir():
                        if not parent.is_dir() or not ordinary(parent) or not SAFE.fullmatch(parent.name) or '..' in parent.name:continue
                        for folder in parent.iterdir():
                            if folder.is_dir() and ordinary(folder) and SAFE.fullmatch(folder.name) and '..' not in folder.name and has_media(folder):ids.add(parent.name+'_'+folder.name)
                for channel in ids-result.keys():
                    try:device,number=parts(channel)
                    except ValueError:continue
                    result[channel]={'channel_id':channel,'device_id':device,'number':number,'name':channel+'（历史数据）','enabled':False,'online':False,'archived':True}
            return sorted(result.values(),key=lambda item:item['archived'])
    def get(self,channel_id):
        item=next((r for r in self.list() if r['channel_id']==channel_id),None)
        if not item:raise ValueError('未知摄像头或历史通道')
        return item

@contextlib.contextmanager
def channel_lock(channel_id,root=ROOT/'channel-locks',timeout=10):
    """One byte file lock: cleanup, event insert and atomic video replacement share it."""
    root=pathlib.Path(root);root.mkdir(parents=True,exist_ok=True)
    if not ordinary(root):raise ValueError('维护锁目录不能是链接')
    path=root/(hashlib.sha256(channel_id.encode()).hexdigest()+'.lock')
    if path.exists() and not ordinary(path):raise ValueError('维护锁不能是链接')
    stream=path.open('a+b');stream.seek(0,2)
    if stream.tell()==0:stream.write(b'0');stream.flush()
    deadline=time.monotonic()+timeout;locked=False
    try:
        while not locked:
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt;msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl;fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                locked=True
            except OSError:
                if time.monotonic()>=deadline:raise ValueError('该通道正在维护，请稍后重试')
                time.sleep(.05)
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt;msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl;fcntl.flock(stream,fcntl.LOCK_UN)
        stream.close()
