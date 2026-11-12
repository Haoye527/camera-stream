const fs=require('fs'),path=require('path'),http=require('http'),cp=require('child_process');
const kind=process.argv[2];if(!/^[a-zA-Z0-9_-]+$/.test(kind||''))throw Error('Invalid relay key');
const config=JSON.parse(fs.readFileSync(path.join(__dirname,'relay-config.json'),'utf8')).relays.find(r=>r.key===kind&&r.type==='tianyi');
if(!config)throw Error('Relay configuration unavailable');
const port=Number(config.port),root=path.resolve(__dirname,config.hls_dir||('relays/'+kind+'/hls'));
if(!root.startsWith(__dirname+path.sep)||!Number.isInteger(port)||port<1024||port>65535)throw Error('Invalid relay configuration');
fs.mkdirSync(root,{recursive:true});
const playlist=path.join(__dirname,kind+'-live.m3u'),log=fs.openSync(path.join(__dirname,kind+'-managed.log'),'a');
const ffmpeg='C:/Users/Administrator/AppData/Local/Programs/Open Design/resources/app/node_modules/@ffmpeg-installer/win32-x64/ffmpeg.exe';
let child=null,stopping=false,retry=null,starting=false,lastStart=0,lastExit=null,lastRenewAttempt=0;
function note(s){fs.writeSync(log,new Date().toISOString()+' '+s+'\n')}
function fresh(){try{return Date.now()-fs.statSync(path.join(root,'live.m3u8')).mtimeMs<20000}catch{return false}}
function launch(){
 if(stopping||starting||child)return;starting=true;
 const useSession=Date.now()-lastRenewAttempt>60000;
 if(useSession)lastRenewAttempt=Date.now();
 const renewArgs=['-NoProfile','-ExecutionPolicy','Bypass','-File',path.join(__dirname,useSession?'renew-tianyi.ps1':'refresh-tianyi.ps1')];
 if(useSession)renewArgs.push('-Camera',kind);
 const refresh=cp.spawn('powershell.exe',renewArgs,{windowsHide:true,stdio:['ignore','ignore',log]});
 const timer=setTimeout(()=>refresh.kill(),30000);
 refresh.on('error',e=>note('refresh failed: '+e.message));
 refresh.on('close',(code)=>{clearTimeout(timer);starting=false;if(stopping)return;
  if(useSession)note('Official session renewal exit='+code);
  const url=fs.readFileSync(playlist,'utf8').split(/\r?\n/).find(x=>x.startsWith('rtsp://'));
  lastStart=Date.now();
  const current=cp.spawn(ffmpeg,['-hide_banner','-loglevel','warning','-rtsp_transport','tcp','-stimeout','15000000','-i',url,'-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-vf','scale=1280:-2','-pix_fmt','yuv420p','-g','30','-sc_threshold','0','-c:a','aac','-ar','16000','-b:a','48k','-f','hls','-hls_time','2','-hls_list_size','6','-hls_flags','delete_segments+omit_endlist','-start_number',String(Math.floor(Date.now()/1000)),'-hls_segment_filename',path.join(root,'segment%09d.ts'),path.join(root,'live.m3u8')],{stdio:['ignore','ignore',log],windowsHide:true});
  child=current;note('FFmpeg started pid='+current.pid);
  fs.writeFileSync(path.join(__dirname,kind+'-managed-process.json'),JSON.stringify({pid:process.pid,ffmpegPid:current.pid,url:'http://127.0.0.1:'+port+'/live.m3u8'}));
  current.on('error',e=>note('FFmpeg error '+e.message));
  current.on('close',(code,signal)=>{child=null;lastExit={code,signal,at:new Date().toISOString()};note('FFmpeg exited '+JSON.stringify(lastExit));if(!stopping)retry=setTimeout(launch,5000)});
 });
}
const server=http.createServer((req,res)=>{
 const name=new URL(req.url,'http://127.0.0.1').pathname.slice(1);
 if(name==='status'){let renewal=null;try{renewal=JSON.parse(fs.readFileSync(path.join(__dirname,kind+'-renew-status.json'),'utf8'))}catch{}res.setHeader('Content-Type','application/json');res.end(JSON.stringify({camera:kind,live:fresh(),ffmpegPid:child?.pid,lastExit,automaticRenewal:true,renewal}));return}
 if(!/^(live\.m3u8|segment\d+\.ts)$/.test(name)){res.writeHead(404);res.end();return}
 if(!fresh()){res.writeHead(503);res.end('Live stream reconnecting');return}
 fs.readFile(path.join(root,name),(e,b)=>{if(e){res.writeHead(404);res.end();return}res.setHeader('Content-Type',name.endsWith('.m3u8')?'application/vnd.apple.mpegurl':'video/mp2t');res.setHeader('Cache-Control','no-store');res.setHeader('Access-Control-Allow-Origin','*');res.end(b)});
});
const watcher=setInterval(()=>{if(child&&Date.now()-lastStart>35000&&!fresh()){note('No fresh HLS; restarting');child.kill()}},5000);
server.listen(port,'127.0.0.1',launch);
function stop(){stopping=true;clearInterval(watcher);clearTimeout(retry);child?.kill();server.close(()=>process.exit());setTimeout(()=>process.exit(),2000).unref()}
process.on('SIGTERM',stop);process.on('SIGINT',stop);
