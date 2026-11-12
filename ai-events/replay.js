/* Real-media implementation of the user's easynvr-replay-v2 reference. */
(function(){
'use strict';
const $=id=>document.getElementById(id),n=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
const TYPE={'0':{label:'画面变化',color:'#8b5cf6',cls:'t-motion'},'1':{label:'人形侦测',color:'#2f7ae0',cls:'t-person'},'2':{label:'车辆侦测',color:'#e8912b',cls:'t-car'},'3':{label:'动物侦测',color:'#1fa97a',cls:'t-animal'}}; // taste-skill-ignore: fixed event-type colors explicitly specified in the user's reference, not a page accent.
Artplayer.PLAYBACK_RATE=[.5,.75,1,1.25,1.5,2,4,8,16];
const DAY=86400,ZOOMS=[30,60,120,300,600,1800,3600,21600,DAY];
let multi;
let cameras=[],events=[],art=null,opened=false,channel=null,date=null,nodeKind='cam',nodeChannel=null,nodeDay=null,selected=null,recordings=[],recordDays=[],media=null,playTime=0,viewStart=0,viewSpan=120,ticket=0,indexTicket=0,busy=false,requestController=null,collapsed=new Set(),sort='desc',filter='all',deepHandled=false,inventorySignature='';
let waitingLatest=null,checkingLatest=false;
let activeSlot=null,preparedSlot=null,retainedSlot=null;
function disposeSlot(slot){if(!slot)return;slot.player.destroy(true);slot.element.remove()}
const stamp=v=>String(v||'').replace('T',' ').slice(0,19),day=e=>stamp(e.start_time).slice(0,10),clock=e=>stamp(e.start_time).slice(11,19),secs=e=>{const p=clock(e).split(':').map(Number);return p[0]*3600+p[1]*60+p[2]+(Number(String(e.start_time).match(/\.(\d+)/)?.[1]||0)/10**(String(e.start_time).match(/\.(\d+)/)?.[1]?.length||0))};
const kind=e=>TYPE[String(e?.event_type)]||{label:'告警事件',color:'#718298',cls:''};
const duration=e=>Math.max(1,Math.round((e.duration||0)/1000));
const byCam=id=>events.filter(e=>e.channel_id===id),camName=id=>cameras.find(c=>c.channel_id===id)?.name||id;
function icon(path){const s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('aria-hidden','true');const p=document.createElementNS(s.namespaceURI,'path');p.setAttribute('d',path);s.append(p);return s}
const dlPath='M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4';
function image(e,cls){const wrap=n('span',undefined,cls);if(e.has_image){const img=n('img');img.src=`/api/events/${e.event_id}/image`;img.alt=camName(e.channel_id)+' '+clock(e)+' '+kind(e).label;img.loading='lazy';img.onerror=()=>{img.remove();wrap.append(n('span','快照不可用'))};wrap.append(img)}else wrap.append(n('span','无快照'));return wrap}
function hms(value){const s=Math.min(DAY,Math.max(0,Math.floor(value)));if(s===DAY)return'24:00';return[String(Math.floor(s/3600)).padStart(2,'0'),String(Math.floor(s%3600/60)).padStart(2,'0'),String(s%60).padStart(2,'0')].join(':')} // taste-skill-ignore: zero padding is necessary for HH:mm:ss clocks.
function notice(message){$('replayMessage').textContent=message||''}
function setUnavailable(title,detail=''){const box=$('replayUnavailable');box.hidden=false;box.querySelector('strong').textContent=title;box.querySelector('span').textContent=detail}
function clearMedia(){waitingLatest=null;++ticket;requestController?.abort();requestController=null;busy=false;disposeSlot(preparedSlot);disposeSlot(retainedSlot);disposeSlot(activeSlot);preparedSlot=retainedSlot=activeSlot=null;art=null;media=null;$('replayMedia').replaceChildren();updateButtons()}
function updateButtons(){if(!$('playBtn'))return;const playing=multi?.enabled?multi.playing:!!art&&!art.video.paused,rate=Number($('speedBtn').dataset.rate||art?.playbackRate||1),muted=multi?.enabled?multi.muted:art?.muted??true;$('playBtn').setAttribute('aria-pressed',String(playing));$('playBtn').setAttribute('aria-label',playing?'暂停录像':'播放录像');$('playIcon').innerHTML=playing?'<path d="M8 5v14M16 5v14"/>':'<path d="M8 5v14l11-7z" fill="currentColor" stroke="none"/>';$('speedBtn').textContent=rate+'×';$('speedResetBtn').hidden=rate===1;$('muteBtn').setAttribute('aria-pressed',String(muted));$('muteBtn').title=muted?'开启声音':'静音'}
function clamp(){viewSpan=Math.min(DAY,Math.max(30,viewSpan));viewStart=Math.min(DAY-viewSpan,Math.max(0,viewStart))}
function center(time){viewStart=time-viewSpan/2;clamp()}
function scale(){const label=viewSpan>=DAY?'24 小时':viewSpan>=3600?(viewSpan/3600)+' 小时':viewSpan>=60?Math.round(viewSpan/60)+' 分钟':viewSpan+' 秒';$('zoomLabel').textContent=label;$('tlScale').replaceChildren();for(let i=0;i<5;i++){let text=hms(viewStart+viewSpan*i/4);if(viewSpan>3600)text=text.slice(0,5);$('tlScale').append(n('span',text))}}
function timeline(){
 if(multi?.enabled){multi.timeline();return}
 if(!opened)return;const box=$('tlEvents');box.replaceChildren();
 for(const r of recordings){const a=Math.max(viewStart,r.start),b=Math.min(viewStart+viewSpan,r.start+r.duration);if(b<=a)continue;const block=n('i',undefined,'tl-record');block.style.left=(a-viewStart)/viewSpan*100+'%';block.style.width=(b-a)/viewSpan*100+'%';block.title='录像 '+hms(r.start)+' 至 '+hms(r.start+r.duration);box.append(block)}
 const intervals=byCam(channel).filter(e=>day(e)===date).map(e=>({e,a:Math.max(viewStart,secs(e)),b:Math.min(viewStart+viewSpan,secs(e)+duration(e))})).filter(r=>r.b>r.a);
 const edges=[...new Set(intervals.flatMap(r=>[r.a,r.b]))].sort((a,b)=>a-b);
 for(let i=0;i<edges.length-1;i++){
  const a=edges[i],b=edges[i+1],hits=intervals.filter(r=>r.a<b&&r.b>a);if(!hits.length)continue;
  const types=[...new Set(hits.map(r=>String(r.e.event_type)))].sort(),colors=types.map(t=>(TYPE[t]||{color:'#718298'}).color);
  const mark=n('i',undefined,'tl-ev'+(hits.some(r=>r.e.event_id===selected)?' active':''));mark.dataset.types=types.join(',');
  mark.style.left=(a-viewStart)/viewSpan*100+'%';mark.style.width=(b-a)/viewSpan*100+'%';
  mark.style.background=colors.length===1?colors[0]:'linear-gradient(to bottom,'+colors.flatMap((c,k)=>[c+' '+k/colors.length*100+'%',c+' '+(k+1)/colors.length*100+'%']).join(',')+')';
  mark.title=hits.map(r=>kind(r.e).label+' · '+clock(r.e)+' 至 '+hms(secs(r.e)+duration(r.e))).join('\n');
  mark.onpointerdown=event=>event.stopPropagation();mark.onclick=event=>{event.stopPropagation();const rect=mark.getBoundingClientRect(),t=types[Math.min(types.length-1,Math.max(0,Math.floor((event.clientY-rect.top)/rect.height*types.length)))],hit=hits.find(r=>String(r.e.event_type)===t);openCamera(hit.e.channel_id,hit.e.event_id)};box.append(mark);
 }
 const active=intervals.find(r=>r.e.event_id===selected);if(active){const outline=n('i',undefined,'tl-selection');outline.style.left=(active.a-viewStart)/viewSpan*100+'%';outline.style.width=(active.b-active.a)/viewSpan*100+'%';outline.style.setProperty('--selection-color',kind(active.e).color);box.append(outline)}
 box.append(n('i',undefined,'tl-play'));box.lastChild.id='tlPlay';scale();paint(false)
}
function paint(follow=true){if(!opened)return;if(follow&&(multi?.enabled?multi.playing:art&&!art.video.paused)&&(playTime<viewStart||playTime>viewStart+viewSpan)){center(playTime);timeline();return}const pointer=$('tlPlay');const position=Math.max(0,Math.min(100,(playTime-viewStart)/viewSpan*100))+'%';if(pointer)pointer.style.left=position;if(multi?.enabled)$('replayLanes').style.setProperty('--play-position',position);playhead();$('timeText').textContent=hms(playTime);$('timeline').setAttribute('aria-valuenow',String(Math.floor(playTime)));$('timeline').setAttribute('aria-valuetext',date+' '+hms(playTime));updateButtons()}
function playhead(){
 const line=$('playheadLine'),master=$('replay')?.querySelector('.replay-master'),lanes=$('replayLanes');
 if(!line)return;
 if(!multi?.enabled||!master||!lanes){line.hidden=true;return}
 const wrap=master.parentElement,box=wrap.getBoundingClientRect(),track=$('timeline').getBoundingClientRect();
 const ratio=Math.min(1,Math.max(0,(playTime-viewStart)/viewSpan));
 line.hidden=false;
 line.style.left=(track.left-box.left+track.width*ratio)+'px';
 line.style.top=master.offsetTop+'px';
 line.style.height=(lanes.offsetTop+lanes.offsetHeight-master.offsetTop)+'px';
}
function downloadLink(e,cls){const a=n('a',undefined,cls);a.append(icon(dlPath));a.href=`/api/events/${e.event_id}/download`;a.download='';a.title='下载事件录像';a.setAttribute('aria-label','下载 '+clock(e)+' '+kind(e).label+'录像');a.onclick=event=>event.stopPropagation();if(e.video_cleared){a.removeAttribute('href');a.removeAttribute('download');a.setAttribute('aria-disabled','true');a.title='录像已清理'}return a}
function tree(){
 const nav=$('treeNav'),scroll=nav.scrollTop;nav.replaceChildren();
 function node(label,count,indent,callback,active,canExpand,key){const wrap=n('div',undefined,'tnode'+(active?' sel':'')+(!canExpand?' no-kids':collapsed.has(key)?'':' open')),button=n('button',undefined,'tnode-btn');wrap.style.setProperty('--indent',indent);button.type='button';const caret=icon('m9 6 6 6-6 6');caret.classList.add('caret');button.append(caret,n('span',label,'tnode-label'),n('span',String(count),'tnode-count'));if(canExpand){button.setAttribute('aria-expanded',String(!collapsed.has(key)));caret.onclick=event=>{event.stopPropagation();collapsed.has(key)?collapsed.delete(key):collapsed.add(key);tree()}}button.onclick=callback;wrap.append(button);return wrap}
 const root=node('全部通道',events.length,0,()=>{nodeKind='all';tree();alerts()},nodeKind==='all',true,'all');nav.append(root);if(collapsed.has('all'))return;
 const children=n('div',undefined,'tnode-kids');root.append(children);root.classList.add('open');
 for(const c of cameras){const list=byCam(c.channel_id),entry=node(c.name,list.length,1,()=>{nodeKind='cam';nodeChannel=c.channel_id;tree();alerts();if(channel!==c.channel_id)openCamera(c.channel_id)},nodeKind==='cam'&&nodeChannel===c.channel_id,true,c.channel_id);children.append(entry);const kids=n('div',undefined,'tnode-kids');entry.append(kids);const days=[...new Set([...list.map(day),...(c.channel_id===channel?recordDays:[])])].sort().reverse();for(const d of days){const child=node(d,list.filter(e=>day(e)===d).length,2,()=>{nodeKind='day';nodeChannel=c.channel_id;nodeDay=d;tree();alerts();if(channel!==c.channel_id||date!==d)openCamera(c.channel_id,null,d,false)},nodeKind==='day'&&nodeChannel===c.channel_id&&nodeDay===d,false);kids.append(child)}if(!days.length)kids.append(n('p','暂无事件','tree-empty'))}nav.scrollTop=scroll;
}
function filteredEvents(){
 const isMulti=multi?.enabled,channelFilter=$('replayChannelFilter')?.value;
 return events.filter(e=>{
  if(filter!=='all'&&String(e.event_type)!==filter)return false;
  if(isMulti)return multi.includes(e.channel_id)&&day(e)===date&&(!channelFilter||e.channel_id===channelFilter);
  return (nodeKind==='all'||e.channel_id===nodeChannel)&&(nodeKind!=='day'||day(e)===nodeDay);
 }).sort((a,b)=>stamp(a.start_time).localeCompare(stamp(b.start_time))*(!isMulti&&sort==='asc'?1:-1));
}
function alerts(){
 const box=$('treeEvents'),scroll=box.scrollTop,list=filteredEvents();box.replaceChildren();
 if(!list.length){box.append(n('p',multi?.enabled?'当前筛选下暂无告警':'该节点下暂无所选类型的事件','tree-empty'));return}
 for(const e of list){
  const row=n('div',undefined,'alert'+(selected===e.event_id?' active':''));
  row.setAttribute('role','option');row.setAttribute('aria-selected',String(selected===e.event_id));
  const button=n('button',undefined,'alert-select'),name=camName(e.channel_id),label=kind(e).label;
  button.type='button';button.setAttribute('aria-label','回放 '+name+' '+stamp(e.start_time)+' '+label);
  const copy=n('span',undefined,'alert-body'),title=n('span',undefined,'alert-title'),dot=n('i',undefined,'adot');
  dot.style.background=kind(e).color;title.append(dot,n('span',label));
  if(String(e.event_type)!=='0')title.append(n('span','AI','ai-badge'));
  const context=multi?.enabled?clock(e)+' · '+name:stamp(e.start_time);
  copy.append(title,n('span',context+' · '+duration(e)+'秒'+(e.video_cleared?' · 录像已清理':''),'alert-meta'));
  button.append(image(e,'alert-shot'),copy);button.onclick=()=>openCamera(e.channel_id,e.event_id);
  row.append(button,downloadLink(e,'alert-dl'));box.append(row);
 }
 box.scrollTop=scroll;
}
function films(){const box=$('filmstrip'),scroll=box.scrollLeft;box.replaceChildren();for(const e of byCam(channel).filter(e=>day(e)===date)){const button=n('button',undefined,'film'+(selected===e.event_id?' active':''));button.type='button';button.title=stamp(e.start_time)+' '+kind(e).label;button.setAttribute('aria-label','播放片段 '+clock(e));button.append(image(e,'film-image'),n('span',clock(e).slice(0,5)));button.onclick=()=>openCamera(channel,e.event_id);box.append(button)}box.scrollLeft=scroll;}
function dock(){const box=$('replayDockShots');box.replaceChildren();for(const e of events.filter(e=>e.has_image).slice(0,5))box.append(image(e,'dock-image'));$('replayDockCount').textContent='已收起 · 共 '+events.length+'条';$('replayDock').hidden=!opened;$('eventList').hidden=opened}
async function index(id,d){const serial=++indexTicket;const query=new URLSearchParams({channel_id:id});if(d)query.set('date',d);const response=await fetch('/api/recordings?'+query,{cache:'no-store'});const value=await response.json();if(!response.ok)throw Error(value.error||'录像索引读取失败');if(serial!==indexTicket||!opened)return false;recordings=value.items;recordDays=value.days;date=value.date;$('replayDate').value=date;return true}
function header(event){$('replayTitle').textContent=camName(channel)+' · 录像回放';$('replayStamp').textContent=event?stamp(event.start_time)+' · '+duration(event)+'秒':date+' · 本机录像';$('replayType').textContent=event?kind(event).label:'连续录像';$('replayType').className='chip '+(event?kind(event).cls:'');const dl=$('dlBtn');if(event&&!event.video_cleared){dl.href=`/api/events/${event.event_id}/download`;dl.download='';dl.removeAttribute('aria-disabled')}else{dl.removeAttribute('href');dl.setAttribute('aria-disabled','true')}}
function createSlot(source){
 const element=n('div',undefined,'replay-player-slot');Object.assign(element.style,{position:'absolute',inset:'0',visibility:'hidden'});$('replayMedia').append(element);
 const player=new Artplayer({container:element,url:source.url,type:'mp4',muted:true,volume:.6,autoplay:false,mutex:false,setting:true,playbackRate:true,fullscreen:true,pip:true,screenshot:true,hotkey:false,theme:'#1765cf',lang:'zh-cn',autoMini:false,moreVideoAttr:{playsInline:true,preload:'auto'}});
 return{element,player,source};
}
function nextRecording(source,end){return recordings.find(r=>r.start>source.start&&r.start+r.duration>end&&r.name!==source.name)}
function prepareNext(){
 if(!opened||busy||!art||!media)return;const end=media.start+art.duration,next=nextRecording(media,end);
 if(!next||next.start-end>=3){disposeSlot(preparedSlot);preparedSlot=null;return}
 if(preparedSlot?.source.url===next.url)return;disposeSlot(preparedSlot);preparedSlot=createSlot(next);
 const slot=preparedSlot;slot.player.once('ready',()=>{if(preparedSlot===slot)slot.player.currentTime=Math.max(0,end-next.start)});
}
async function playSource(source,target,autoplay=true){
 waitingLatest=null;if(!source){clearMedia();setUnavailable('这个时间没有本机录像','请切换日期或选择其他事件');return}
 const desiredRate=Number($('speedBtn').dataset.rate||art?.playbackRate||1),muted=art?.muted??true,volume=art?.volume??.6;
 const serial=++ticket;requestController?.abort();requestController=null;disposeSlot(retainedSlot);retainedSlot=activeSlot;
 if(retainedSlot)retainedSlot.player.pause();
 if(preparedSlot?.source.url===source.url){activeSlot=preparedSlot;preparedSlot=null}else{disposeSlot(preparedSlot);preparedSlot=null;activeSlot=createSlot(source)}
 const slot=activeSlot,player=slot.player;art=player;media=source;busy=true;notice('');
 if(retainedSlot)$('replayUnavailable').hidden=true;else setUnavailable('正在加载录像…');
 if(source.download&&!selected){$('dlBtn').href=source.download;$('dlBtn').download='';$('dlBtn').removeAttribute('aria-disabled')}
 let initialized=false,shown=false;
 const reveal=()=>{if(serial!==ticket||shown||!initialized||player.video.seeking||player.video.readyState<2)return;shown=true;slot.element.style.visibility='visible';$('replayUnavailable').hidden=true;disposeSlot(retainedSlot);retainedSlot=null;paint();prepareNext()};
 const initialize=()=>{if(serial!==ticket||initialized)return;initialized=true;busy=false;player.muted=muted;player.volume=volume;player.currentTime=Math.max(0,Math.min(target-source.start,Math.max(0,player.duration-.05)));const rate=Number($('speedBtn').dataset.rate||desiredRate);player.playbackRate=rate;$('speedBtn').dataset.rate=rate;
  if(autoplay)player.play().catch(()=>{notice('录像已就绪，点击播放继续');reveal()});else reveal();paint()};
 player.on('video:playing',reveal);player.on('video:seeked',()=>{if(!autoplay||!player.video.paused)reveal()});player.on('video:loadeddata',()=>{if(!autoplay)reveal()});
 player.on('video:timeupdate',()=>{if(serial!==ticket||busy)return;playTime=source.start+player.currentTime;paint();if(shown)prepareNext()});player.on('video:play',updateButtons);player.on('video:pause',updateButtons);player.on('video:volumechange',updateButtons);player.on('video:ratechange',()=>{if(serial!==ticket||busy)return;$('speedBtn').dataset.rate=player.playbackRate;updateButtons()});
 player.on('video:ended',()=>{if(serial!==ticket)return;const end=source.start+player.duration,next=nextRecording(source,end);if(next&&next.start-end<3){playTime=Math.max(next.start,end);playSource(next,playTime,true)}else{waitingLatest={channel,ticket:serial,name:source.name,startTime:source.start_time||date+' '+hms(source.start)};loadLatest()}});
 player.on('error',()=>{if(serial!==ticket)return;busy=false;setUnavailable('这段录像暂时无法播放','文件可能已清理或仍在生成，请刷新后重试');notice('录像播放失败')});
 if(player.isReady)initialize();else player.once('ready',initialize);
}
async function loadLatest(){
 const pending=waitingLatest;if(!pending||checkingLatest||!opened)return;checkingLatest=true;
 try{
  const response=await fetch('/api/recordings?'+new URLSearchParams({channel_id:pending.channel}),{cache:'no-store'});
  const value=await response.json();if(!response.ok)throw Error(value.error||'录像索引读取失败');
  if(waitingLatest!==pending||!opened||channel!==pending.channel||ticket!==pending.ticket)return;
  const latest=value.items[value.items.length-1];
  if(!latest||latest.name===pending.name||latest.start_time<=pending.startTime){notice('等待最新录像生成，生成后自动播放…');return}
  recordings=value.items;recordDays=value.days;date=value.date;$('replayDate').value=date;selected=null;
  if(nodeKind==='day'){nodeDay=date;nodeChannel=channel}
  playTime=latest.start;center(playTime);header(null);tree();alerts();films();timeline();
  await playSource(latest,latest.start,true);notice('已自动加载最新录像：'+latest.start_time);
 }catch(error){if(waitingLatest===pending)notice('最新录像暂时无法读取，正在自动重试：'+error.message)}
 finally{checkingLatest=false}
}
function recordingAt(time){return recordings.find(r=>time>=r.start&&time<r.start+r.duration)}
async function playEventClip(event,target,autoplay){
 clearMedia();notice('');paint(false);
 if(event.video_cleared){setUnavailable('该事件录像已清理','事件快照和时间仍可在右侧查看');return}
 setUnavailable('正在读取事件录像…');
 const serial=ticket,controller=new AbortController();requestController=controller;
 try{
  const result=await fetch(`/api/events/${event.event_id}/video-info`,{cache:'no-store',signal:controller.signal});
  const info=await result.json();if(serial!==ticket||controller.signal.aborted)return;
  if(!result.ok){setUnavailable(result.status===404?'该事件时段没有本机录像':'事件录像暂时无法读取',info.error);return}
  const offset=info.offset||0;
  const time=info.start_time?.slice(0,10)===date?secs(info):secs(event)-offset;
  await playSource({url:info.url,start:time,duration:info.duration,name:'event-'+event.event_id},target,autoplay);
 }catch(error){if(serial===ticket&&!controller.signal.aborted)setUnavailable('事件录像暂时无法读取',error.message)}
 finally{if(requestController===controller)requestController=null}
}
async function seek(time,autoplay=true,fromTimeline=false,event=null,exact=false){
 if(multi?.enabled){multi.seek(time,autoplay,fromTimeline);return}
 waitingLatest=null;requestController?.abort();requestController=null;
 playTime=Math.max(0,Math.min(DAY-.01,time));
 let source=recordingAt(playTime),gap=false;
 if(fromTimeline){
  event=byCam(channel).find(e=>day(e)===date&&playTime>=secs(e)&&playTime<secs(e)+duration(e));
  selected=event?.event_id||null;header(event);alerts();films();timeline();
 }
 if(event&&!source){await playEventClip(event,playTime,autoplay);return}
 if(!source&&recordings.length&&!exact){
  source=recordings.reduce((best,r)=>Math.abs(r.start-playTime)<Math.abs(best.start-playTime)?r:best);
  playTime=source.start;gap=true;
 }
 if(source&&media?.name===source.name&&art&&!busy){
  art.currentTime=playTime-source.start;if(autoplay)art.play().catch(()=>{});
 }else await playSource(source,playTime,autoplay);
 if(source?.download&&!selected){$('dlBtn').href=source.download;$('dlBtn').download='';$('dlBtn').removeAttribute('aria-disabled')}
 notice(gap?'所选时段无录像，已定位最近可用片段':'');paint();
}
async function openCamera(id,eventId,d,autoplay=true,at=null,exact=false){
 if(multi?.enabled)return multi.open(id,eventId,d,autoplay,at);
 if(!cameras.some(c=>c.channel_id===id))return;const e=events.find(e=>e.event_id===eventId)||(!d?byCam(id)[0]:null);opened=true;channel=id;nodeChannel=id;if(nodeKind!=='all'&&nodeKind!=='day')nodeKind='cam';if(e){selected=e.event_id;d=day(e)}else selected=null;
 $('replay').hidden=false;$('replay').classList.add('is-open');$('replay').setAttribute('aria-hidden','false');dock();header(e);clearMedia();setUnavailable('正在读取录像…');
 const request=ticket;
 try{
  if(!await index(id,d)||request!==ticket)return;
  const target=Number.isFinite(at)?Math.max(0,Math.min(DAY-1,at)):e?secs(e):(recordings.length?recordings[recordings.length-1].start:0);
  playTime=target;viewSpan=120;center(target+(e?duration(e)/2:0));header(e);tree();alerts();films();timeline();
  await seek(target,autoplay,false,e,exact);
 }catch(error){if(request===ticket){clearMedia();setUnavailable('未找到可播放的本机录像',error.message);notice(error.message)}}
}
function close(){if(!opened)return;opened=false;++indexTicket;multi?.close();clearMedia();$('replay').hidden=true;$('replay').classList.remove('is-open');$('replay').setAttribute('aria-hidden','true');dock()}
function zoom(dir,anchor=.5){let i=ZOOMS.findIndex(v=>v>=viewSpan);i=Math.max(0,Math.min(ZOOMS.length-1,i+dir));const time=viewStart+viewSpan*anchor;viewSpan=ZOOMS[i];viewStart=time-viewSpan*anchor;clamp();timeline()}

function multiview(){
 const sizes={2:2,4:2,6:3,9:3,16:4};
 const grid=n('div',undefined,'replay-grid'),lanes=n('div',undefined,'replay-lanes'),toolbar=n('div',undefined,'replay-layouts');
 const chooser=n('dialog',undefined,'replay-picker'),options=n('div',undefined,'replay-pick-options'),pickTitle=n('h2','选择摄像头');
 const choose=n('button','选择摄像头','mini-btn'),remove=n('button','移除此通道'),cancel=n('button','取消');
 const calibrate=n('button','校时','mini-btn'),calibration=n('dialog',undefined,'replay-picker'),offsetForm=n('form');
 const offsetTitle=n('h2','回放校时'),offsetLabel=n('label','画面校时（秒）'),offsetInput=n('input'),offsetReset=n('button','恢复默认'),offsetSave=n('button','保存'),offsetCancel=n('button','取消');
 const offsetHelp=n('p','正值让此路读取稍晚的画面。设置按摄像头和日期保存。');
 offsetInput.type='number';offsetInput.min='-30';offsetInput.max='30';offsetInput.step='.1';offsetInput.required=true;offsetInput.id='replayOffset';offsetLabel.htmlFor=offsetInput.id;
 offsetReset.type=offsetCancel.type='button';offsetSave.type='submit';calibrate.type='button';calibrate.hidden=true;
 calibration.setAttribute('aria-label','回放校时');offsetForm.append(offsetTitle,offsetHelp,offsetLabel,offsetInput,offsetReset,offsetCancel,offsetSave);calibration.append(offsetForm);
 const channelFilter=n('select');
 channelFilter.id='replayChannelFilter';channelFilter.setAttribute('aria-label','选择通道');channelFilter.onchange=alerts;
 $('replaySort').after(channelFilter);
 grid.hidden=lanes.hidden=choose.hidden=true;lanes.id='replayLanes';
 grid.setAttribute('aria-label','多路回放画面');lanes.setAttribute('aria-label','各路录像覆盖');
 toolbar.setAttribute('role','group');toolbar.setAttribute('aria-label','回放分屏');toolbar.append(n('span','分屏'));
 chooser.setAttribute('aria-label','选择摄像头');chooser.append(pickTitle,options,remove,cancel);
 $('stage').before(grid);$('timeline').parentElement.after(lanes);$('replay').append(chooser,calibration);$('replay').querySelector('.replay-top').after(toolbar);
 let count=1,slots=[],active=0,playing=false,muted=true,rate=1,anchor=0,anchorTime=0;
 let request=null,clipRequest=null,clip=null,indexes=new Map(),indexDate=null,lastEnd=0;
 let clickTimer=null;
 const views=new Map();
 const calibrationKey='easynvr-replay-offsets';
 let defaults={},offsets={},calibrationChannel=null;
 try{const saved=JSON.parse(localStorage.getItem(calibrationKey));if(saved&&typeof saved==='object')offsets=saved}catch{}
 const offsetFor=id=>{
  const value=offsets[date]?.[id]??defaults[date]?.[id]??0;
  return Number.isFinite(value)&&Math.abs(value)<=30?value:0;
 };
 const cameraTime=id=>playTime+offsetFor(id);
 fetch('/replay-sync.json',{cache:'no-store'}).then(response=>response.ok?response.json():{}).then(value=>{
  defaults=value||{};
  if(count>1){updateEnd();timeline();sync(true)}
 }).catch(()=>{});
 const currentIds=()=>slots.slice(0,count).filter(Boolean);
 const includes=id=>currentIds().includes(id);
 const rebase=()=>{anchor=playTime;anchorTime=performance.now()};
 const cancelTileClick=()=>{clearTimeout(clickTimer);clickTimer=null};
 const end=r=>r.start+r.duration;
 const covers=(r,time)=>r&&time>=r.start&&time<end(r);
 const clockTime=()=>Math.max(playTime,Math.min(DAY-.01,lastEnd,anchor+(performance.now()-anchorTime)*rate/1000));
 const activeRecord=id=>{
  const time=cameraTime(id);
  return indexes.get(id)?.items.find(r=>covers(r,time))||(clip?.id===id&&covers(clip.source,time)?clip.source:null);
 };
 function updateEnd(){
  lastEnd=Math.max(0,...[...indexes].map(([id,value])=>value.items.reduce((max,row)=>Math.max(max,end(row)-offsetFor(id)),0)),clip?end(clip.source)-offsetFor(clip.id):0);
 }
 const countButtons=new Map([1,...Object.keys(sizes).map(Number)].map(value=>{
  const button=n('button',value+' 路','mini-btn');button.type='button';button.setAttribute('aria-pressed',String(value===1));button.onclick=()=>switchCount(value);toolbar.append(button);return [value,button];
 }));
 choose.type=remove.type=cancel.type='button';toolbar.append(calibrate,choose);
 choose.onclick=()=>openPicker(active);
 cancel.onclick=()=>chooser.close();
 calibrate.onclick=()=>{
  calibrationChannel=slots[active]||channel;
  if(!calibrationChannel)return;
  offsetTitle.textContent=camName(calibrationChannel)+' · 回放校时';
  offsetInput.value=offsetFor(calibrationChannel);
  calibration.showModal();
 };
 offsetReset.onclick=()=>{offsetInput.value=defaults[date]?.[calibrationChannel]??0};
 offsetCancel.onclick=()=>calibration.close();
 offsetForm.onsubmit=event=>{
  event.preventDefault();
  if(!offsets[date]||typeof offsets[date]!=='object')offsets[date]={};
  offsets[date][calibrationChannel]=offsetInput.valueAsNumber;
  try{localStorage.setItem(calibrationKey,JSON.stringify(offsets))}catch{}
  calibration.close();updateEnd();timeline();sync(true);
 };

 function resetVideo(view){
  for(const slot of view.decoders){
   slot.video.pause();slot.video.hidden=true;
   if(slot.source){slot.source=null;slot.video.removeAttribute('src');slot.video.load()}
  }
  view.current=view.visible=null;
 }
 function newView(id){
  const tile=n('div',undefined,'replay-tile'),label=n('button',camName(id),'replay-tile-name'),state=n('span','正在读取录像…','replay-tile-state'),time=n('time',undefined,'replay-tile-time');
  const view={id,tile,label,state,time,decoders:[],current:null,visible:null};
  for(let i=0;i<2;i++){
   const video=n('video'),slot={video,source:null,failed:false};
   video.playsInline=true;video.muted=true;video.preload='auto';video.hidden=true;
   video.addEventListener('loadedmetadata',()=>{if(view.current===slot)syncView(view,true)});
   for(const event of ['loadeddata','seeked','canplay','ended'])video.addEventListener(event,()=>{if(view.current===slot)syncView(view,false)});
   video.addEventListener('error',()=>{
    if(!slot.source)return;
    slot.failed=true;
    if(view.current!==slot)return;
    state.textContent='录像暂时无法播放';state.hidden=false;
   });
   view.decoders.push(slot);tile.append(video);
  }
  label.type='button';
  tile.title='单击播放或暂停，双击切换到此路单屏回放';
  let resumeOnDoubleClick=false;
  tile.onclick=event=>{
   selectActive(slots.indexOf(id),true);
   if(event.detail===1){
    resumeOnDoubleClick=playing;
    cancelTileClick();
    clickTimer=setTimeout(()=>{
     clickTimer=null;
     clipRequest?.abort();
     setPlaying(!resumeOnDoubleClick);
    },280);
   }
  };
  tile.ondblclick=()=>switchCount(1,id,resumeOnDoubleClick);
  tile.dataset.channel=id;tile.append(state,label,time);
  return view;
 }
 const adjacentGap=1.25;
 function nextRecord(items,source){
  return items.find(item=>item.start>source.start&&end(item)>end(source));
 }
 function loadDecoder(slot,source){
  slot.video.pause();slot.video.muted=true;slot.video.hidden=true;
  slot.source=source;slot.failed=false;slot.video.src=source.url;
 }
 function prepare(view,next){
  if(!next)return;
  const standby=view.decoders.find(slot=>slot!==view.current&&slot!==view.visible);
  if(standby&&standby.source?.url!==next.url)loadDecoder(standby,next);
 }
 function syncView(view,force){
  const {id,state,time}=view,entry=indexes.get(id),items=entry?.items||[],target=cameraTime(id);
  const current=view.current?.source;
  let source=activeRecord(id);
  if(covers(current,target)&&current.url!==clip?.source.url&&items.some(r=>r.url===current.url)&&!view.current.video.ended)source=current;
  if(!source){
   const outgoing=view.visible?.source,next=outgoing&&nextRecord(items,outgoing);
   if(!force&&next&&target>=end(outgoing)&&target<next.start&&next.start-end(outgoing)<=adjacentGap){
    view.visible.video.pause();prepare(view,next);
    state.textContent='片段间隔';state.hidden=false;time.textContent=hms(playTime);
    return;
   }
   resetVideo(view);state.hidden=false;time.hidden=true;
   state.textContent=entry?.error||(!entry&&request?'正在读取录像…':'此时段无录像');
   return;
  }
  time.hidden=false;time.textContent=hms(playTime);
  if(current?.url!==source.url){
   const previous=view.visible;
   const adjacent=!force&&previous?.source&&source.start>previous.source.start&&source.start-end(previous.source)<=adjacentGap;
   if(previous){previous.video.pause();previous.video.muted=true;if(!adjacent){previous.video.hidden=true;view.visible=null}}
   const slot=view.decoders.find(decoder=>decoder.source?.url===source.url&&!decoder.failed)||view.decoders.find(decoder=>decoder!==view.visible);
   view.current=slot;
   if(slot.source?.url!==source.url||slot.failed)loadDecoder(slot,source);
   state.hidden=!!view.visible;state.textContent='正在加载录像…';
  }
  const slot=view.current,video=slot.video;
  if(slot.failed||video.readyState===0)return;
  const offset=Math.min(Math.max(0,target-source.start),Math.max(0,video.duration-.04));
  const difference=Math.abs(video.currentTime-offset);
  const tolerance=(force||view.visible!==slot) ? .03 : Math.max(.3,rate*.15);
  if(!video.seeking&&difference>tolerance)video.currentTime=offset;
  if(!video.seeking&&video.readyState>=2){
   if(view.visible&&view.visible!==slot)view.visible.video.hidden=true;
   view.visible=slot;video.hidden=false;state.hidden=true;
  }
  video.muted=muted||id!==slots[active];
  if(video.playbackRate!==rate)video.playbackRate=rate;
  if(playing&&video.paused&&!video.ended)video.play().catch(()=>{});
  if(!playing&&!video.paused)video.pause();
  const next=nextRecord(items,source);
  if(playing&&next&&next.start-end(source)<=adjacentGap&&end(source)-target<Math.max(5,rate*2))prepare(view,next);
 }
 function sync(force=false){
  for(const view of views.values())syncView(view,force);
  if(!selected){
   const download=activeRecord(slots[active])?.download,link=$('dlBtn');
   if(download){link.href=download;link.download='';link.removeAttribute('aria-disabled')}
   else{link.removeAttribute('href');link.setAttribute('aria-disabled','true')}
  }
  paint();
 }
 function selectActive(index,redraw=false){
  active=Math.max(0,index);
  channel=slots[active]||currentIds()[0]||null;
  calibrate.disabled=!channel;
  recordDays=indexes.get(channel)?.days||[];
  for(const view of views.values()){
   view.tile.classList.toggle('active',view.id===slots[active]);
   view.label.setAttribute('aria-pressed',String(view.id===slots[active]));
   if(view.current)view.current.video.muted=muted||view.id!==slots[active];
  }
  if(redraw){timeline();sync()}
 }
 function render(){
  const ids=new Set(currentIds());
  for(const [id,view] of views)if(!ids.has(id)){resetVideo(view);views.delete(id)}
  grid.style.setProperty('--replay-cols',sizes[count]);
  grid.style.setProperty('--replay-aspect',sizes[count]/Math.ceil(count/sizes[count])*16/9);
  const tiles=Array.from({length:count},(_,index)=>{
   const id=slots[index];
   if(id){
    if(!views.has(id))views.set(id,newView(id));
    const view=views.get(id);view.label.textContent=camName(id);return view.tile;
   }
   const button=n('button','＋ 添加摄像头','replay-tile replay-tile-empty');button.type='button';button.setAttribute('aria-label','在画面 '+(index+1)+' 添加摄像头');button.onclick=()=>openPicker(index);return button;
  });
  tiles.forEach((tile,index)=>{if(grid.children[index]!==tile)grid.insertBefore(tile,grid.children[index]||null)});
  while(grid.children.length>count)grid.lastElementChild.remove();
  if(selected&&!includes(events.find(event=>event.event_id===selected)?.channel_id))selected=null;
  selectActive(active);renderDetails();sync();
 }
 function renderDetails(){
  const event=events.find(item=>item.event_id===selected);
  header(event);$('replayTitle').textContent=count+' 路录像回放';$('replayStamp').textContent=currentIds().length+' 路已选 · 同步回放';
  if(!event)$('replayType').textContent='同步回放';
  updateChannelFilter();alerts();timeline();
 }
 function updateChannelFilter(){
  const ids=currentIds(),entries=[['','全部通道'],...ids.map(id=>[id,camName(id)])];
  const existing=Array.from(channelFilter.options);
  if(existing.length===entries.length&&entries.every(([id,name],i)=>existing[i].value===id&&existing[i].text===name))return;
  const previous=channelFilter.value;
  channelFilter.replaceChildren(...entries.map(([id,name])=>new Option(name,id)));
  if(ids.includes(previous))channelFilter.value=previous;
 }
 function openPicker(index){
  pickTitle.textContent='为画面 '+(index+1)+' 选择摄像头';
  options.replaceChildren(...cameras.map(camera=>{
   const button=n('button',camera.name+(camera.archived?'（历史）':''));
   button.type='button';button.onclick=()=>{
    const previous=slots.indexOf(camera.channel_id);
    if(previous!==-1)slots[previous]=slots[index];
    slots[index]=camera.channel_id;active=index;
    chooser.close();render();refresh();
   };
   return button;
  }));
  remove.hidden=!slots[index];remove.onclick=()=>{slots[index]=null;active=index;chooser.close();render();refresh()};
  chooser.showModal();
 }
 async function refresh(){
  if(count===1||!date)return;
  request?.abort();
  const controller=new AbortController(),requestedDate=date;
  request=controller;
  if(indexDate!==date){indexDate=date;indexes=new Map()}
  const ids=currentIds();
  const values=await Promise.all(ids.map(async id=>{
   try{
    const response=await fetch('/api/recordings?'+new URLSearchParams({channel_id:id,date:requestedDate}),{cache:'no-store',signal:controller.signal});
    const value=await response.json();if(!response.ok)throw Error(value.error);
    return [id,value];
   }catch{return [id,indexes.get(id)||{items:[],days:[],error:'录像索引暂不可用'}]}
  }));
  if(controller.signal.aborted)return;
  request=null;indexes=new Map(values);
  updateEnd();
  selectActive(active);renderDetails();sync();
 }
 function drawTimeline(){
  const box=$('tlEvents'),pointer=n('i',undefined,'tl-play');
  pointer.id='tlPlay';box.replaceChildren(pointer);
  lanes.replaceChildren(...currentIds().map(id=>{
   const row=n('div',undefined,'replay-lane'),label=n('button',camName(id),'replay-lane-name'),track=n('div',undefined,'replay-lane-track');
   label.type='button';label.title=camName(id);label.onclick=()=>selectActive(slots.indexOf(id),true);label.setAttribute('aria-pressed',String(id===slots[active]));
   function block(start,finish,cls,title,event=null){
    const correction=offsetFor(id),a=Math.max(viewStart,start-correction),b=Math.min(viewStart+viewSpan,finish-correction);if(b<=a)return;
    const element=n(event?'button':'i',undefined,cls);
    element.style.left=(a-viewStart)/viewSpan*100+'%';element.style.width=(b-a)/viewSpan*100+'%';
    if(event){
     element.type='button';element.style.background=kind(event).color;
     element.setAttribute('aria-label',camName(id)+' '+title);
     element.onclick=()=>open(id,event.event_id,null,true);
    }
    element.title=title;track.append(element);
   }
   for(const item of indexes.get(id)?.items||[])block(item.start,end(item),'tl-record','录像 '+hms(item.start)+' 至 '+hms(end(item)));
   if(clip?.id===id)block(clip.source.start,end(clip.source),'tl-record','事件录像');
   for(const event of byCam(id).filter(e=>day(e)===date))block(secs(event),secs(event)+duration(event),'tl-ev',kind(event).label+' · '+clock(event),event);
   track.append(n('i',undefined,'replay-lane-needle'));
   row.append(label,track);return row;
  }));
  scale();paint(false);
 }
 function setPlaying(value){
  if(playing)playTime=clockTime();
  playing=value;rebase();sync();
 }
 function jump(time,autoplay,fromTimeline){
  cancelTileClick();
  if(fromTimeline){selected=null;clipRequest?.abort()}
  playTime=Math.max(0,Math.min(DAY-.01,time));playing=autoplay;rebase();
  if(playTime<viewStart||playTime>viewStart+viewSpan)center(playTime);
  if(fromTimeline)renderDetails();
  sync(true);
 }
 async function open(id,eventId,d,autoplay,at){
  if(!cameras.some(camera=>camera.channel_id===id))return;
  const event=events.find(e=>e.event_id===eventId);
  if(!includes(id)){
   const target=Array.from({length:count},(_,i)=>i).find(i=>!slots[i])??active,previous=slots.indexOf(id);
   if(previous>=0)slots[previous]=slots[target];
   slots[target]=id;
  }
  active=slots.indexOf(id);
  const nextDate=event?day(event):d||date;
  if(nextDate!==date){date=nextDate;indexes=new Map();lastEnd=0;clip=null;for(const view of views.values())resetVideo(view)}
  $('replayDate').value=date;
  selected=event?.event_id||null;
  const time=Number.isFinite(at)?at:event?secs(event)-offsetFor(id):playTime;
  clipRequest?.abort();clip=null;
  const controller=new AbortController();clipRequest=controller;
  jump(time,false,false);render();await refresh();
  if(controller.signal.aborted)return;
  if(event&&!event.video_cleared&&!activeRecord(id)){
   try{
    const response=await fetch('/api/events/'+event.event_id+'/video-info',{cache:'no-store',signal:controller.signal});
    const info=await response.json();
    if(response.ok&&!controller.signal.aborted){
     const start=info.start_time?.slice(0,10)===date?secs(info):time+offsetFor(id)-(info.offset||0);
     clip={id,source:{start,duration:info.duration,url:info.url}};
     updateEnd();timeline();
    }
   }catch{}
  }
  if(!controller.signal.aborted)jump(time,autoplay,false);
 }
 function switchCount(next,selectedChannel,resume){
  if(next===count||!opened)return;
  cancelTileClick();
  clipRequest?.abort();
  const autoplay=resume??(count===1?!!art&&!art.video.paused:playing);
  if(next===1){
   const id=selectedChannel||slots[active]||currentIds()[0]||cameras[0]?.channel_id;
   const at=(playing?clockTime():playTime)+offsetFor(id);
   const event=events.find(e=>e.event_id===selected&&e.channel_id===id&&day(e)===date&&at>=secs(e)&&at<secs(e)+duration(e));
   closeMulti();
   nodeKind='cam';
   if(id)openCamera(id,event?.event_id,date,autoplay,at,true);
   return;
  }
  if(count===1){
   if(!date||!channel)return;
   slots=[channel,...cameras.filter(c=>c.channel_id!==channel&&!c.archived).map(c=>c.channel_id)];
   active=0;indexes=new Map([[channel,{items:recordings,days:recordDays}]]);indexDate=date;
   playTime=Math.max(0,Math.min(DAY-.01,playTime-offsetFor(channel)));
   if(playTime<viewStart||playTime>viewStart+viewSpan)center(playTime);
   updateEnd();
   muted=art?.muted??true;clearMedia();++indexTicket;notice('');
  }
  count=next;active=Math.min(active,count-1);$('replay').classList.add('is-multi');
  grid.hidden=lanes.hidden=choose.hidden=calibrate.hidden=false;$('stage').hidden=true;
  countButtons.forEach((button,value)=>button.setAttribute('aria-pressed',String(count===value)));
  rate=Number($('speedBtn').dataset.rate||1);playing=autoplay;rebase();render();refresh();
 }
 function closeMulti(){
  if(count===1)return;
  cancelTileClick();
  request?.abort();clipRequest?.abort();request=clipRequest=null;clip=null;
  if(chooser.open)chooser.close();
  if(calibration.open)calibration.close();
  if(document.fullscreenElement===$('replay'))document.exitFullscreen().catch(()=>{});
  for(const view of views.values())resetVideo(view);
  views.clear();indexes.clear();grid.replaceChildren();lanes.replaceChildren();
  grid.hidden=lanes.hidden=choose.hidden=calibrate.hidden=true;$('stage').hidden=false;
  $('replay').classList.remove('is-multi');count=1;playing=false;
  countButtons.forEach((button,value)=>button.setAttribute('aria-pressed',String(value===1)));
 }
 setInterval(()=>{
  if(!opened||count===1||!playing)return;
  playTime=clockTime();
  if(playTime>=lastEnd)rebase();
  sync();
 },200);
 return{
  get enabled(){return count>1},get playing(){return playing},get muted(){return muted},
  includes,open,seek:jump,close:closeMulti,timeline:drawTimeline,refresh,
  hold(){cancelTileClick();setPlaying(false)},
  toggle(){cancelTileClick();setPlaying(!playing)},
  rate(value){if(playing)playTime=clockTime();rate=value;rebase();sync()},
  mute(){muted=!muted;sync()},
  update(){slots=slots.map(id=>cameras.some(c=>c.channel_id===id)?id:null);render()},
 };
}
function init(markup){$('replayMount').innerHTML=markup;$('legend').replaceChildren();for(const t of Object.values(TYPE)){const item=n('span',undefined,'lg'),dot=n('i');dot.style.background=t.color;item.append(dot,n('span',t.label));$('legend').append(item)}
 const reset=n('button','复位','mini-btn');reset.id='speedResetBtn';reset.type='button';reset.hidden=true;reset.title='恢复正常速度（1×）';reset.setAttribute('aria-label','恢复1倍速');$('speedBtn').after(reset);
 const tl=$('timeline'),master=n('div',undefined,'replay-master');
 $('replay').querySelector('.ctrl').after(master);master.append(n('span','总进度','replay-master-label'),tl);tl.setAttribute('aria-label','录像总进度');
 const setRate=value=>{$('speedBtn').dataset.rate=value;if(multi.enabled)multi.rate(value);else if(art)art.playbackRate=value;updateButtons()};
 reset.onclick=()=>{setRate(1);$('speedBtn').focus({preventScroll:true})};
 multi=multiview();
 const playLine=n('i',undefined,'playhead-line');playLine.id='playheadLine';playLine.hidden=true;$('replay').querySelector('.stage-wrap').append(playLine);
 window.addEventListener('resize',()=>{if(opened)playhead()});
 $('replay').style.scrollMarginTop='16px';$('replayNav').onclick=async event=>{event.preventDefault();if(!opened){const id=cameras.some(c=>c.channel_id===channel)?channel:cameras.find(c=>!c.archived)?.channel_id||cameras[0]?.channel_id;if(!id)return;await openCamera(id)}$('replay').scrollIntoView({block:'start',behavior:'auto'});$('replayTitle').setAttribute('tabindex','-1');$('replayTitle').focus({preventScroll:true})};
 $('speedBtn').title='切换播放速度：0.5×、1×、2×、4×、8×、16×';$('closeReplay').onclick=close;$('expandEventDock').onclick=close;$('replayDate').onchange=()=>{nodeKind='day';nodeDay=$('replayDate').value;if(nodeDay)openCamera(channel||cameras[0]?.channel_id,null,nodeDay,false)};$('replayFilter').onchange=()=>{filter=$('replayFilter').value;alerts()};$('replaySort').onchange=()=>{sort=$('replaySort').value;alerts()};$('playBtn').onclick=()=>{if(multi.enabled){multi.toggle();return}waitingLatest=null;if(art){if(art.video.paused)art.play().catch(()=>notice('请重新选择录像'));else art.pause()}};
 $('speedBtn').onclick=()=>{const values=[1,2,4,8,16,.5],rate=Number($('speedBtn').dataset.rate||1);setRate(values[(values.indexOf(rate)+1)%values.length])};$('muteBtn').onclick=()=>{if(multi.enabled)multi.mute();else if(art)art.muted=!art.muted;updateButtons()};$('fsBtn').onclick=()=>{if(multi.enabled){const action=document.fullscreenElement?document.exitFullscreen():$('replay').requestFullscreen();action.catch(()=>notice('当前无法切换全屏'))}else if(art)art.fullscreen=!art.fullscreen};$('zoomIn').onclick=()=>zoom(1,(playTime-viewStart)/viewSpan);$('zoomOut').onclick=()=>zoom(-1,(playTime-viewStart)/viewSpan);$('zoomLabel').onclick=()=>{viewSpan=viewSpan===DAY?120:DAY;center(playTime);timeline()};
 let dragging=false,dragAutoplay=true,dragStart=0,pinch=null,pinchFrame=0,resumeAfterPinch=false;
 const pointers=new Map();
 const position=event=>{const rect=tl.getBoundingClientRect();return viewStart+Math.min(1,Math.max(0,(event.clientX-rect.left)/rect.width))*viewSpan};
 const clampSpan=value=>Math.min(DAY,Math.max(30,value));
 const pinchGeometry=()=>{
  const points=[...pointers.values()];if(points.length<2)return null;
  const [a,b]=points,rect=tl.getBoundingClientRect(),mid=(a.x+b.x)/2;
  return {rect,distance:Math.max(1,Math.hypot(b.x-a.x,b.y-a.y)),mid,time:viewStart+Math.min(1,Math.max(0,(mid-rect.left)/rect.width))*viewSpan};
 };
 const beginPinch=()=>{
  const geometry=pinchGeometry();if(!geometry)return;
  resumeAfterPinch=multi.enabled?multi.playing:!!art&&!art.video.paused;
  if(multi.enabled)multi.hold();else if(art)art.pause();
  pinch={distance:geometry.distance,span:viewSpan,time:geometry.time};
 };
 const updatePinch=()=>{
  if(!pinch)return;
  const geometry=pinchGeometry();if(!geometry)return;
  viewSpan=clampSpan(pinch.span*pinch.distance/geometry.distance);
  viewStart=pinch.time-(geometry.mid-geometry.rect.left)/geometry.rect.width*viewSpan;
  clamp();
  if(!pinchFrame)pinchFrame=requestAnimationFrame(()=>{pinchFrame=0;timeline()});
 };
 const endPinch=()=>{
  pinch=null;
  if(!resumeAfterPinch)return;
  resumeAfterPinch=false;
  if(multi.enabled)multi.toggle();else if(art)art.play().catch(()=>{});
 };
 tl.onpointerdown=event=>{
  if(!opened)return;event.preventDefault();
  pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  try{tl.setPointerCapture(event.pointerId)}catch{}
  if(pointers.size===2){dragging=false;beginPinch();return}
  if(pointers.size>2)return;
  dragging=true;dragStart=playTime;dragAutoplay=!multi.enabled||multi.playing;if(multi.enabled)multi.hold();playTime=position(event);paint(false)
 };
 tl.onpointermove=event=>{
  if(!pointers.has(event.pointerId))return;
  pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
  if(pointers.size>=2){updatePinch();return}
  if(dragging){playTime=position(event);paint(false)}
 };
 tl.onpointerup=event=>{
  pointers.delete(event.pointerId);
  try{tl.releasePointerCapture(event.pointerId)}catch{}
  if(pinch){if(pointers.size<2){dragging=false;endPinch()}return}
  if(!dragging)return;dragging=false;seek(position(event),dragAutoplay,true)
 };
 tl.onpointercancel=event=>{
  pointers.delete(event.pointerId);
  if(pinch){if(pointers.size<2){dragging=false;endPinch()}return}
  if(!dragging)return;dragging=false;if(multi.enabled)multi.seek(dragStart,dragAutoplay,false)
 };
 tl.addEventListener('wheel',event=>{event.preventDefault();const r=tl.getBoundingClientRect();zoom(event.deltaY<0?-1:1,Math.min(1,Math.max(0,(event.clientX-r.left)/r.width)))},{passive:false});
 tl.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();seek(event.key==='Home'?0:event.key==='End'?DAY-1:playTime+(event.key==='ArrowRight'?2:-2),!multi.enabled||multi.playing,true)}};
 document.addEventListener('keydown',event=>{if(event.target.matches('input,textarea,select'))return;if(event.key==='Escape'&&opened)close();if(event.key===' '&&opened&&!event.target.closest('button,a')){event.preventDefault();$('playBtn').click()}});
}
/* Markup is local application code, inserted by the build step below. */
const MARKUP="  <section class=\"replay\" id=\"replay\" hidden aria-hidden=\"true\" aria-label=\"录像回放工作区\">\n    <div class=\"replay-card\">\n      <div class=\"replay-top\">\n        <div class=\"replay-id\">\n          <span class=\"live-dot\" aria-hidden=\"true\"></span>\n          <span class=\"replay-title\" id=\"replayTitle\">录像回放</span>\n          <span class=\"chip\" id=\"replayType\"></span>\n          <span class=\"replay-stamp\" id=\"replayStamp\"></span><input id=\"replayDate\" type=\"date\" aria-label=\"录像日期\">\n          <span class=\"legend\" id=\"legend\" aria-hidden=\"true\"></span>\n        </div>\n        <div class=\"replay-tools\">\n          <a class=\"ghost-btn\" id=\"dlBtn\" href=\"#\" aria-label=\"下载事件录像\">\n            <svg viewBox=\"0 0 24 24\"><path d=\"M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4\"/></svg>\n            下载\n          </a>\n          <button class=\"close-btn\" id=\"closeReplay\" type=\"button\">收起回放</button>\n        </div>\n      </div>\n\n      <div class=\"replay-body\">\n        <div class=\"stage-wrap\">\n          <div class=\"stage\" id=\"stage\"><div id=\"replayMedia\"></div><div id=\"replayUnavailable\" hidden><strong>正在加载录像…</strong><span></span></div></div><p id=\"replayMessage\" role=\"status\"></p>\n          <div class=\"ctrl\">\n            <button class=\"icon-btn\" id=\"playBtn\" type=\"button\" aria-label=\"播放/暂停\">\n              <svg id=\"playIcon\" viewBox=\"0 0 24 24\"><path d=\"M8 5v14l11-7z\" fill=\"currentColor\" stroke=\"none\"/></svg>\n            </button>\n            <span class=\"ctrl-time\" id=\"timeText\">00:00 / 00:00</span>\n            <div class=\"timeline\" id=\"timeline\" role=\"slider\" aria-label=\"24 小时时间轴\" tabindex=\"0\"\n                 aria-valuemin=\"0\" aria-valuemax=\"86400\" aria-valuenow=\"0\">\n              <div class=\"tl-rail\">\n                <div id=\"tlEvents\"></div>\n              </div>\n              <div class=\"tl-scale\" id=\"tlScale\" aria-hidden=\"true\"></div>\n            </div>\n            <div class=\"zoom-box\">\n              <button class=\"zoom-btn\" id=\"zoomOut\" type=\"button\" title=\"减少时间范围（滚轮也可）\" aria-label=\"减少时间轴范围\">−</button>\n              <button class=\"zoom-label\" id=\"zoomLabel\" type=\"button\" title=\"在 2 分钟与 24 小时之间切换\">2 分钟</button>\n              <button class=\"zoom-btn\" id=\"zoomIn\" type=\"button\" title=\"增加时间范围（滚轮也可）\" aria-label=\"增加时间轴范围\">＋</button>\n            </div>\n            <button class=\"mini-btn\" id=\"speedBtn\" type=\"button\" title=\"播放速度\">1×</button>\n            <button class=\"icon-btn\" id=\"muteBtn\" type=\"button\" title=\"静音\" aria-label=\"静音\">\n              <svg viewBox=\"0 0 24 24\"><path d=\"M11 5 6 9H3v6h3l5 4z\"/><path d=\"M16 9a4 4 0 0 1 0 6\"/></svg>\n            </button>\n            <button class=\"icon-btn\" id=\"fsBtn\" type=\"button\" title=\"全屏\" aria-label=\"全屏\">\n              <svg viewBox=\"0 0 24 24\"><path d=\"M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5\"/></svg>\n            </button>\n          </div>\n\n          <div class=\"filmstrip\" id=\"filmstrip\" aria-label=\"事件片段\"></div>\n        </div>\n\n        <aside class=\"tree\" aria-label=\"事件树\">\n          <div class=\"tree-head\">\n            <strong>事件树</strong>\n            <span class=\"tree-head-tools\"><select id=\"replayFilter\" aria-label=\"筛选事件类型\"><option value=\"all\">全部类型</option><option value=\"0\">画面变化</option><option value=\"1\">人形侦测</option><option value=\"3\">动物侦测</option><option value=\"2\">车辆侦测</option></select><select id=\"replaySort\" aria-label=\"事件时间排序\"><option value=\"desc\">从新到旧</option><option value=\"asc\">从旧到新</option></select></span>\n          </div>\n          <div class=\"tree-cols\">\n            <div class=\"tree-col\" id=\"treeNav\" role=\"tree\" aria-label=\"通道与日期\"></div>\n            <div class=\"tree-col\" id=\"treeEvents\" role=\"listbox\" aria-label=\"告警事件\"></div>\n          </div>\n        </aside>\n      </div>\n    </div>\n  </section>\n\n";
init(MARKUP);
setInterval(()=>{if(waitingLatest)loadLatest()},5000);
setInterval(()=>{if(!opened)return;if(multi.enabled){multi.refresh();return}if(!busy&&!waitingLatest){const id=channel,d=date;index(id,d).then(ok=>{if(ok&&!multi.enabled&&channel===id&&date===d){tree();timeline()}}).catch(()=>{})}},30000);
window.Replay={open:openCamera,close,update(value){const signature=JSON.stringify(value);if(signature===inventorySignature)return;inventorySignature=signature;cameras=value.cameras;events=value.events;dock();if(opened){if(multi.enabled)multi.update();else{if(!cameras.some(c=>c.channel_id===channel)){close();return}header(events.find(e=>e.event_id===selected));tree();alerts();films();timeline()}}if(!deepHandled){deepHandled=true;const q=new URLSearchParams(location.search),id=q.get('replay'),d=q.get('date'),at=q.get('at');if(id&&cameras.some(c=>c.channel_id===id)){const validDate=/^\d{4}-\d{2}-\d{2}$/.test(d||'')?d:undefined,validTime=/^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$/.test(at||'');openCamera(id,null,validDate,true,validTime?at.split(':').reduce((n,v)=>n*60+Number(v),0):null)}else if(location.hash==='#replay'){document.getElementById('replayNav').click()}}}};
})();
