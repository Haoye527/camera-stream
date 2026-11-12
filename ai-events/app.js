'use strict';
let events=[],cameras=[],selected=null,working=false,lastRender='',playRequest=0;
let hoverOwner=null,hoverTimer=null,hoverRequest=0,hoverController=null;
let storageData=null,cleanupPlan=null,cleaning=false,cleanupRequest=0;
let adaptiveToken='',adaptiveBusy=false;
const $=id=>document.getElementById(id);
const hasEventPreview=Boolean($('eventList'));
function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e}
function downloadIcon(){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4');svg.append(path);return svg}
function dateLabel(value){return String(value||'').replace('T',' ').slice(0,19)}
function layoutState(){
 const key='easynvr-event-layout';
 try{const saved=JSON.parse(localStorage.getItem(key));if(saved&&[1,4,6].includes(saved.layout)&&Array.isArray(saved.slots))return {layout:saved.layout,slots:saved.slots.map(v=>typeof v==='string'?v:null),ready:true}}catch{}
 return {layout:4,slots:[],ready:false};
}
const layoutInit=layoutState();
let layout=layoutInit.layout,slots=layoutInit.slots,layoutReady=layoutInit.ready;
function saveLayout(){try{localStorage.setItem('easynvr-event-layout',JSON.stringify({layout,slots}))}catch{}}
function cameraById(id){return cameras.find(c=>c.channel_id===id)}
function syncSlots(){
 const known=new Set(cameras.map(c=>c.channel_id));
 const used=new Set();
 slots=slots.map(id=>{if(!id||!known.has(id)||used.has(id))return null;used.add(id);return id});
 while(slots.length<layout)slots.push(null);
 slots.length=layout;
 if(!layoutReady){
  fillEmptySlots();
  layoutReady=true;saveLayout();
 }
}
function fillEmptySlots(){
 const taken=new Set(slots.filter(Boolean));
 for(let index=0;index<layout;index++){
  if(slots[index])continue;
  const next=cameras.find(camera=>!taken.has(camera.channel_id));
  if(!next)break;
  slots[index]=next.channel_id;taken.add(next.channel_id);
 }
}
function setLayout(next){
 if(next===layout)return;
 const keep=slots.filter(Boolean).slice(0,next);
 layout=next;slots=keep;syncSlots();fillEmptySlots();saveLayout();renderList();
}
function addCamera(index,id){
 if(!cameraById(id))return;
 slots=slots.map((value,i)=>i!==index&&value===id?null:value);
 slots[index]=id;
 syncSlots();saveLayout();renderList();
}
function removeSlot(index){slots[index]=null;saveLayout();renderList()}
function emptySlot(index){
 const section=node('section',undefined,'camera-group empty-slot');
 const add=node('button',undefined,'add-slot');add.type='button';
 add.append(node('span','＋'),node('span','添加摄像头'));
 add.setAttribute('aria-label','为第 '+(index+1)+' 个位置添加摄像头');
 add.onclick=()=>openPicker(index);
 section.append(add);return section;
}
let pickIndex=0;
function openPicker(index){
 pickIndex=index;
 $('pickerTitle').textContent='添加摄像头 · 第 '+(index+1)+' 个位置';
 const box=$('pickerOptions');box.replaceChildren();
 const used=new Set(slots.filter(Boolean));
 if(!cameras.length)box.append(node('p','暂无可添加的摄像头','picker-empty'));
 for(const camera of cameras){
  const option=node('button',undefined,'picker-option');option.type='button';
  option.append(node('strong',camera.name),node('span',(camera.total||0)+' 条事件'+(camera.archived?' · 历史数据':'')));
  if(used.has(camera.channel_id)){option.disabled=true;option.append(node('em','已在页面'))}
  option.onclick=()=>{addCamera(pickIndex,camera.channel_id);$('cameraPicker').close()};
  box.append(option);
 }
 $('cameraPicker').showModal();
}
function renderList(){
 closeCleanupMenus();document.querySelectorAll('.camera-cleanup-menu').forEach(e=>e.remove());
 const list=$('eventList'),scrolls=new Map(Array.from(list.querySelectorAll('.camera-events')).map(el=>[el.dataset.camera,el.scrollTop]));list.replaceChildren();
 list.dataset.layout=String(layout);
 for(let index=0;index<layout;index++){
  const camera=cameraById(slots[index]);
  if(!camera){list.append(emptySlot(index));continue}
  const group=node('section',undefined,'camera-group');group.setAttribute('aria-label',camera.name+'事件时间');
  const heading=node('div',undefined,'camera-heading');heading.append(node('h2',camera.name),node('span',camera.total+'条'));
  const drop=node('button',undefined,'slot-remove');drop.type='button';drop.title='从页面移除，不影响录像与事件';drop.setAttribute('aria-label','从页面移除'+camera.name);
  drop.innerHTML='<span>移除</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  drop.onclick=()=>removeSlot(index);heading.append(drop);
  group.append(heading);
  const tools=node('div',undefined,'camera-storage');tools.dataset.storageChannel=camera.channel_id;const usage=node('span','正在检测…','camera-usage'),holder=node('div',undefined,'camera-cleanup-holder'),clear=node('button','清理','clear-camera');clear.type='button';clear.disabled=!storageData;clear.setAttribute('aria-label',camera.name+'清理菜单');clear.setAttribute('aria-haspopup','menu');clear.setAttribute('aria-expanded','false');
  const menu=node('div',undefined,'camera-cleanup-menu');menu.hidden=true;menu.setAttribute('role','menu');
  for(const [mode,label,hint] of [['all','全部清空','录像、事件记录与图片'],['videos','清空录像 · 保留事件','保留事件录像、快照和记录']]){const action=node('button',label);action.type='button';action.setAttribute('role','menuitem');action.append(node('small',hint));action.onclick=()=>openCleanup(camera,mode);menu.append(action)}
  document.body.append(menu);clear.onclick=event=>{event.stopPropagation();const show=menu.hidden;closeCleanupMenus();menu.hidden=!show;clear.setAttribute('aria-expanded',String(show));const rect=clear.getBoundingClientRect();menu.style.left=Math.max(12,Math.min(innerWidth-250,rect.right-238))+'px';menu.style.top=(rect.bottom+8)+'px'};holder.append(clear);tools.append(usage,holder);group.append(tools);
  const rows=events.filter(e=>e.channel_id===camera.channel_id),timeline=node('div',undefined,'camera-events');timeline.dataset.camera=camera.channel_id;
  if(!rows.length)timeline.append(node('p','暂无事件','group-empty'));
  for(const e of rows){
   const card=node('article',undefined,'event-row'+(e.event_id===selected?' selected':''));const button=node('button',undefined,'event-play');button.type='button';button.setAttribute('aria-pressed',e.event_id===selected?'true':'false');
   button.setAttribute('aria-label',`播放 ${e.name} ${dateLabel(e.start_time)} 的事件录像`);
   const picture=node('span',undefined,'picture');
   if(e.has_image){const image=node('img');image.src=`/api/events/${e.event_id}/image`;image.alt=e.name+' 事件图片';image.loading='lazy';picture.append(image)}else picture.append(node('span','图片缺失','thumb-missing'));
   const play=node('span',undefined,'play-marker');play.setAttribute('aria-hidden','true');const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M8 5v14l11-7z');svg.append(path);play.append(svg);picture.append(play);button.append(picture);
   picture.addEventListener('mouseenter',()=>scheduleHover(e,picture));picture.addEventListener('mouseleave',stopHover);
   const meta=node('span',undefined,'event-meta');const stamp=node('span');stamp.append(node('time',dateLabel(e.start_time)),node('span',Math.round((e.duration||0)/1000)+'秒 · '+({'0':'画面变化','1':'人形侦测','2':'车辆侦测','3':'动物侦测'}[String(e.event_type)]||'事件'),'duration'));const download=node('a',undefined,'download-link');download.append(downloadIcon());download.title='下载事件录像';download.href=`/api/events/${e.event_id}/download`;download.setAttribute('download','');download.setAttribute('aria-label',`下载 ${e.name} ${dateLabel(e.start_time)} 的事件录像`);meta.append(stamp,download);
   if(e.video_cleared){stamp.append(node('span','录像已清理','video-cleared'));download.removeAttribute('href');download.removeAttribute('download');download.setAttribute('aria-disabled','true');download.title='录像已清理，保留事件图片和时间'}
   button.onclick=()=>openPlayback(e);card.append(button,meta);timeline.append(card);
  }
  group.append(timeline);if(camera.total>camera.shown)group.append(node('p',`显示最近${camera.shown}条事件`,'group-empty'));list.append(group);timeline.scrollTop=scrolls.get(camera.channel_id)||0;
 }
 document.querySelectorAll('.layout-switch button').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.layout)===layout)));
 const shownCameras=cameras.filter(camera=>slots.includes(camera.channel_id)),shownCount=shownCameras.reduce((sum,camera)=>sum+camera.total,0);
 $('counts').textContent=`${shownCameras.length} / ${layout} 路 · 共 ${cameras.length} 路可用 · ${shownCount} 个事件`;
 updateCameraStorage();
}
function closeCleanupMenus(){document.querySelectorAll('.camera-cleanup-menu').forEach(e=>e.hidden=true);document.querySelectorAll('.clear-camera').forEach(e=>e.setAttribute('aria-expanded','false'))}
document.addEventListener('click',event=>{if(!event.target.closest('.camera-cleanup-holder'))closeCleanupMenus()});document.addEventListener('keydown',event=>{if(event.key==='Escape')closeCleanupMenus()});
function bytesLabel(value){if(value<1024)return value+' B';const units=['KB','MB','GB','TB'];let size=value/1024,index=0;while(size>=1024&&index<units.length-1){size/=1024;index++}return size.toFixed(size<10?1:0)+' '+units[index]}
function updateCameraStorage(){
 const count=storageData?.cameras.length||0;document.querySelectorAll('.global-cleanup').forEach(e=>e.disabled=!count||cleaning);
 $('clearPreviewCache').disabled=!storageData?.csrf||cleaning;
 for(const element of document.querySelectorAll('.camera-storage')){const data=storageData?.cameras.find(c=>c.channel_id===element.dataset.storageChannel);element.querySelector('.camera-usage').textContent=data?`${data.videos} 段录像 · ${bytesLabel(data.bytes)}`:'暂不可用';element.querySelector('button').disabled=!data||cleaning}
}
async function refreshStorage(){
 try{
  const response=await fetch('/api/storage',{cache:'no-store'}),data=await response.json();
  if(!response.ok)throw Error(data.error||'空间检测失败');
  storageData=data;
  const disk=data.disk,percent=disk.total?Math.round(disk.used/disk.total*100):null;
  const recordingBytes=data.cameras.every(camera=>Number.isFinite(camera.video_bytes))?data.cameras.reduce((sum,camera)=>sum+camera.video_bytes,0):null;
  $('diskSummary').textContent=`${bytesLabel(disk.free)} 可用`;
  $('diskSummary').title=`录像占用 ${recordingBytes===null?'—':bytesLabel(recordingBytes)} · ${disk.path}`;
  $('diskDetail').textContent=`${bytesLabel(disk.used)} / ${bytesLabel(disk.total)}`;
  $('diskPercent').textContent=percent===null?'—':`${percent}%`;
  $('diskMeter').max=disk.total||1;$('diskMeter').value=disk.used;
  $('storagePanel').classList.toggle('low-space',disk.total>0&&disk.free/disk.total<.1);
  updateCameraStorage();
 }catch(error){
  $('diskSummary').textContent='暂不可用';$('diskSummary').title=error.message;
  $('diskDetail').textContent='—';$('diskPercent').textContent='—';$('diskMeter').value=0;
  $('storagePanel').classList.remove('low-space');storageData=null;updateCameraStorage();
 }
}
async function storagePost(route,body){const response=await fetch('/api/storage/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':storageData?.csrf||''},body:JSON.stringify(body)}),data=await response.json();if(!response.ok)throw Error(data.error||'操作失败');return data}
async function clearPreviewCache(){
 if(cleaning||!storageData?.csrf)return;
 cleaning=true;
 updateCameraStorage();
 const notice=$('storageNotice');
 notice.hidden=false;
 notice.textContent='正在清理预览缓存…';
 try{
  const result=await storagePost('clear-preview-cache',{});
  notice.textContent=result.removed?`已清理 ${result.removed} 个缓存文件，释放 ${bytesLabel(result.released)}。`:'暂无可清理的预览缓存。';
  if(result.skipped)notice.textContent+=`另有 ${result.skipped} 个文件正在使用或暂时无法清理。`;
  await refreshStorage();
 }catch(error){notice.textContent='清理预览缓存失败。'+error.message}
 finally{cleaning=false;updateCameraStorage()}
}
async function openCleanup(camera,mode='all'){
 const request=++cleanupRequest;closeCleanupMenus();const scope=camera||{channel_id:'all',name:'全部摄像头'},keep=mode==='videos';
 if(hasEventPreview){stopHover();window.Replay.close();if($('playerDialog').open)$('playerDialog').close()}
 cleanupPlan=null;$('cleanupName').value='';$('confirmCleanup').disabled=true;$('cleanupMessage').textContent='正在核对录像及事件…';$('cleanupTitle').textContent=(keep?'清空录像 · 保留事件':'全部清空')+' · '+scope.name;$('cleanupScope').textContent='';$('cleanupTargets').textContent='';$('cleanupExpected').textContent=scope.name;$('cleanupDialog').showModal();
 try{const plan=await storagePost('prepare-cleanup',{channel_id:scope.channel_id,mode});if(request!==cleanupRequest||!$('cleanupDialog').open)return;cleanupPlan=plan;$('cleanupExpected').textContent=plan.name;$('cleanupScope').textContent=`${plan.camera_count} 路 · ${plan.events} 条事件 · ${plan.files} 个文件 · 约 ${bytesLabel(plan.bytes)}`;
 $('cleanupTargets').textContent=plan.cameras.map(c=>c.name).join('、');$('cleanupMessage').textContent=(keep?`删除未关联事件的录像，保留事件记录、快照及覆盖事件时段的 ${plan.protected_videos} 段录像。`:'永久删除本次范围内的本机录像、事件记录、事件图片及关联缓存。')+'摄像头、连接配置和录像计划保留，后续仍会生成新数据。正在写入或变化的文件可能保留。';$('cleanupName').focus()}
 catch(error){$('cleanupMessage').textContent=error.message}
}

async function confirmCleanup(){
 if(cleaning||!cleanupPlan||$('cleanupName').value!==cleanupPlan.name)return;
 cleaning=true;if(hasEventPreview)stopHover();$('confirmCleanup').disabled=true;$('cancelCleanup').disabled=true;$('cleanupName').disabled=true;$('cleanupMessage').textContent='正在清理，请稍候…';updateCameraStorage();
 try{const result=await storagePost('cleanup',{token:cleanupPlan.token,channel_id:cleanupPlan.channel_id,confirmation:$('cleanupName').value});$('cleanupDialog').close();$('storageNotice').hidden=false;$('storageNotice').textContent=`${cleanupPlan.name}：已清理 ${result.removed} 个文件，释放 ${bytesLabel(result.released)}。`+(result.mode==='videos'?`保留 ${result.kept_events} 条事件，${result.playable_events} 条有录像。`:`已移除 ${result.events} 条事件。`)+(result.skipped?`另有 ${result.skipped} 个文件被占用或发生变化。`:'');lastRender='';if(hasEventPreview)await refresh();await refreshStorage()}
 catch(error){cleanupPlan=null;$('cleanupMessage').textContent=error.message+'。请取消后重新打开清理窗口。';if(hasEventPreview)await refresh();await refreshStorage()}
 finally{cleaning=false;$('cancelCleanup').disabled=false;$('cleanupName').disabled=false;updateCameraStorage()}
}
function stopHover(){
 ++hoverRequest;clearTimeout(hoverTimer);hoverTimer=null;hoverOwner=null;hoverController?.abort();hoverController=null;
 const video=$('hoverVideo');video.onerror=null;video.onloadedmetadata=null;video.pause();video.removeAttribute('src');video.removeAttribute('poster');video.load();$('hoverPreview').hidden=true;
}
function scheduleHover(e,picture){
 if(!matchMedia('(hover: hover) and (pointer: fine)').matches||$('playerDialog').open||$('cleanupDialog').open)return;
 stopHover();hoverOwner=picture;const request=hoverRequest;
 hoverTimer=setTimeout(()=>startHover(e,picture,request),220);
}
async function startHover(e,picture,request){
 if(request!==hoverRequest||hoverOwner!==picture||!picture.isConnected)return;
 const preview=$('hoverPreview'),video=$('hoverVideo'),rect=picture.getBoundingClientRect(),width=Math.min(1200,innerWidth*.72,(innerHeight-144)*16/9),height=width*9/16+100;
 let left=rect.right+12;if(left+width>innerWidth-16)left=rect.left-width-12;if(left<16)left=(innerWidth-width)/2;
 preview.style.width=width+'px';preview.style.left=left+'px';preview.style.top=Math.max(16,Math.min(innerHeight-height-16,rect.top-16))+'px';
 $('hoverTitle').textContent=e.name;$('hoverTime').textContent=dateLabel(e.start_time);$('hoverMessage').textContent='正在加载事件录像…';video.muted=true;video.defaultMuted=true;video.hidden=false;if(e.has_image)video.poster=`/api/events/${e.event_id}/image`;preview.hidden=false;
 const controller=new AbortController();hoverController=controller;
 try{
  const response=await fetch(`/api/events/${e.event_id}/video-info`,{cache:'no-store',signal:controller.signal}),info=await response.json();if(!response.ok)throw Error(info.error||'事件录像暂时不可用');if(request!==hoverRequest)return;
  video.onerror=()=>{if(request===hoverRequest)$('hoverMessage').textContent='事件录像暂时无法播放。'};video.onloadedmetadata=()=>{if(request===hoverRequest)$('hoverMessage').textContent='静音播放 · 移开鼠标关闭'};video.src=info.url;
  try{await video.play()}catch(error){if(request===hoverRequest&&error.name!=='AbortError')$('hoverMessage').textContent='点击图片打开完整播放器。'}
 }catch(error){if(request===hoverRequest&&error.name!=='AbortError')$('hoverMessage').textContent=error.message}
}
function openPlayback(e){stopHover();window.Replay.open(e.channel_id,e.event_id)}
async function refresh(){
 if(working)return;working=true;$('refresh').disabled=true;
 try{
  const response=await fetch('/api/events',{cache:'no-store'}),data=await response.json();if(!response.ok)throw Error(data.error||'事件读取失败');
  events=data.events;cameras=data.cameras;window.Replay.update(data);
  syncSlots();
  if(!events.some(e=>e.event_id===selected))selected=null;
$('sourceError').hidden=true;
  const next=JSON.stringify(data);if(next!==lastRender&&!hoverOwner){lastRender=next;renderList()}
 }catch(e){$('sourceError').hidden=false;$('sourceError').textContent=e.message}
 finally{working=false;$('refresh').disabled=false}
}
if(hasEventPreview){
$('closePlayer').onclick=()=>$('playerDialog').close();
$('playerDialog').addEventListener('close',()=>{++playRequest;const video=$('eventVideo');video.onerror=null;video.onloadedmetadata=null;video.pause();video.removeAttribute('src');video.load();document.querySelector('.event-play[aria-pressed="true"]')?.focus({preventScroll:true})});
window.addEventListener('blur',stopHover);window.addEventListener('resize',stopHover);document.addEventListener('visibilitychange',()=>{if(document.hidden)stopHover()});
$('layoutSwitch').addEventListener('click',event=>{const button=event.target.closest('button[data-layout]');if(button)setLayout(Number(button.dataset.layout))});
$('pickerCancel').onclick=()=>$('cameraPicker').close();
}
$('cleanupName').addEventListener('input',()=>{$('confirmCleanup').disabled=cleaning||!cleanupPlan||$('cleanupName').value!==cleanupPlan.name});
$('cleanupDialog').addEventListener('close',()=>{++cleanupRequest});
$('confirmCleanup').onclick=confirmCleanup;$('cancelCleanup').onclick=()=>$('cleanupDialog').close();$('cleanupDialog').addEventListener('cancel',event=>{if(cleaning)event.preventDefault()});
function showAdaptive(data){
 adaptiveToken=data.csrf||'';const toggle=$('adaptiveToggle');toggle.checked=!!data.enabled;toggle.disabled=adaptiveBusy||!data.available;
 let status='已关闭';
 if(!data.available)status='未安装';
 else if(data.enabled)status=!data.healthy?'服务暂不可用':data.errors?'部分需重试':'运行中';
 else if(data.processing)status='关闭中';
 $('adaptiveState').textContent=status;
 const totals=data.totals||{};
 $('adaptiveCount').textContent=data.available?(totals.files||0).toLocaleString('zh-CN'):'—';
 $('adaptiveSaved').textContent=data.available?`节省 ${bytesLabel(totals.saved_bytes||0)}`:'—';
 $('adaptiveSaved').title='统计原录像与优化后录像的差额';
}
async function refreshAdaptive(){
 if(adaptiveBusy)return;
 try{const reply=await fetch('/api/adaptive-recording',{cache:'no-store'});if(!reply.ok)throw Error('设置读取失败');showAdaptive(await reply.json())}
 catch(error){$('adaptiveState').textContent='智能录像服务暂不可用';$('adaptiveToggle').disabled=true}
}
$('adaptiveToggle').addEventListener('change',async()=>{
 if(adaptiveBusy)return;adaptiveBusy=true;const toggle=$('adaptiveToggle'),enabled=toggle.checked;toggle.disabled=true;$('adaptiveState').textContent='正在保存…';
 try{const reply=await fetch('/api/adaptive-recording',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':adaptiveToken},body:JSON.stringify({enabled})});const data=await reply.json();if(!reply.ok)throw Error(data.error||'设置保存失败');adaptiveBusy=false;showAdaptive(data)}
 catch(error){toggle.checked=!enabled;$('adaptiveState').textContent=error.message;adaptiveBusy=false;toggle.disabled=false}
});
function refreshConsole(){refreshStorage();refreshAdaptive()}
if(hasEventPreview){
 $('refresh').onclick=()=>{refresh();refreshConsole()};
 refresh();
 setInterval(refresh,1000);
}else $('liveRefresh').addEventListener('click',refreshConsole);
refreshConsole();setInterval(refreshStorage,30000);setInterval(refreshAdaptive,5000);

$('cleanupAll').onclick=()=>openCleanup(null,'all');$('cleanupAllVideos').onclick=()=>openCleanup(null,'videos');
$('clearPreviewCache').onclick=clearPreviewCache;