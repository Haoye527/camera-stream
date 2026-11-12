"""Start configured cloud adapters only when an enabled EasyNVR channel uses them."""
import contextlib,json,os,pathlib,re,socket,subprocess,sys,time
from urllib.parse import urlsplit
from camera_registry import Registry
ROOT=pathlib.Path(__file__).resolve().parent

def local_path(value):
    path=(ROOT/value).resolve()
    if not path.is_relative_to(ROOT):raise ValueError('Adapter path outside project')
    return path

def configured():
    rows=json.loads((ROOT/'relay-config.json').read_text(encoding='utf8'))['relays'];keys=set();ports=set()
    for row in rows:
        key=row['key'];port=row['port']
        if not re.fullmatch(r'[A-Za-z0-9_-]+',key) or key in keys or type(port) is not int or not 1024<=port<=65535 or port in ports:raise ValueError('Invalid or duplicate relay configuration')
        if row['type'] not in ('tianyi','yi'):raise ValueError('Unknown adapter type')
        keys.add(key);ports.add(port)
    return rows

def desired(rows,cameras):
    ports=set()
    for camera in cameras:
        if not camera['enabled']:continue
        try:
            url=urlsplit(camera.get('source_url',''))
            if url.hostname in ('localhost','127.0.0.1','::1'):ports.add(url.port)
        except ValueError:continue
    return {row['key']:row for row in rows if row['port'] in ports}

def command(row):
    if row['type']=='tianyi':return [r'C:\Program Files\nodejs\node.exe',str(ROOT/'tianyi-relay.cjs'),row['key']]
    work=local_path(row.get('work_dir','relays/'+row['key']));work.mkdir(parents=True,exist_ok=True)
    return [str(ROOT/'python32/python.exe'),str(ROOT/'local-camera-hub/serve_live.py'),str(row.get('quality',1)),'--port',str(row['port']),'--workdir',str(work),'--config',str(local_path(row['config']))]

def listening(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.3):return True
    except OSError:return False

def stop(child):
    if child.poll() is None:
        # Only a still-owned Popen child and its descendants are stopped.
        subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000,timeout=15)
        child.wait(timeout=15)

def publish_status(file,state):
    # Windows readers/antivirus can temporarily hold the destination file.
    # Telemetry failure must never stop owned video adapters.
    pending=file.with_name(file.name+f'.{os.getpid()}.tmp')
    try:
        pending.write_text(json.dumps(state),encoding='utf8')
        for attempt in range(5):
            try:pending.replace(file);return True
            except PermissionError:
                if attempt==4:raise
                time.sleep(.1)
    except OSError as error:
        print(f'Status publication deferred: {error}',file=sys.stderr,flush=True)
        return False

def main():
    statefile=ROOT/'relay-manager-status.json';children={};next_attempt={}
    (ROOT/'relay-manager-process.json').write_text(json.dumps({'pid':os.getpid()}))
    try:
        while not (ROOT/'relay-manager-stop.request').exists():
            state={'pid':os.getpid(),'updated_at':time.time(),'relays':{}}
            try:
                targets=desired(configured(),Registry().list(history=False,private=True))
                for key,(child,signature) in list(children.items()):
                    if key not in targets or signature!=json.dumps(targets[key],sort_keys=True):stop(child);children.pop(key)
                for key,row in targets.items():
                    existing=children.get(key)
                    if existing and existing[0].poll() is not None:children.pop(key);next_attempt[key]=time.monotonic()+5;existing=None
                    if not existing and not listening(row['port']) and time.monotonic()>=next_attempt.get(key,0):
                        with (ROOT/(key+'-supervisor.log')).open('ab') as log:
                            child=subprocess.Popen(command(row),cwd=ROOT,stdout=log,stderr=log,creationflags=0x08000000)
                        children[key]=(child,json.dumps(row,sort_keys=True));existing=children[key]
                    state['relays'][key]={'port':row['port'],'listening':listening(row['port']),'owned':bool(existing),'pid':existing[0].pid if existing else None}
            except Exception as error:state['error']=type(error).__name__+': '+str(error)
            publish_status(statefile,state);time.sleep(5)
    finally:
        for child,_ in children.values():stop(child)

if __name__=='__main__':main()
