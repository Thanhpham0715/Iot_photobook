'use strict';
const $=id=>document.getElementById(id);
const isAdmin=document.querySelector('meta[name=page]').content==='admin';
const csrf=document.querySelector('meta[name=csrf-token]').content;
const states={stopped:'Đã dừng',waiting:'Đang chờ người',ready:'Sẵn sàng',capturing:'Đang chụp'};
let status=null, previousState='', settingsLoaded=false, optionsKey='', sessionKey='', previewUrl=null, noticeTimer;
let gestureLogKey='';
const gestureNames={Open_Palm:'Bàn tay mở',Thumb_Up:'Giơ ngón cái',Victory:'Chữ V',Pointing_Up:'Trỏ lên',Closed_Fist:'Nắm tay',None:'Chưa xác định'};
let selectionPending=0, selectionVersion=0, selectionQueue=Promise.resolve();
function notice(text){$('notice').textContent=text;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{$('notice').textContent='';},8000);}
async function api(path,method='GET',data){
 const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
 const result=await response.json(); if(!response.ok)throw new Error(result.error||'Yêu cầu thất bại');return result;
}
function bind(id,fn){if($(id))$(id).addEventListener('click',async()=>{try{await fn();await refresh();}catch(e){notice(e.message);}});}
function fillSelect(node,items,value){node.replaceChildren(...items.map(i=>{const o=document.createElement('option');o.value=i.id;o.textContent=i.label;return o;}));if(items.some(i=>i.id===value))node.value=value;}
function setupSettings(s){
 if(settingsLoaded)return;
 const form=$('settingsForm');for(const [k,v]of Object.entries(s.settings)){if(form.elements[k])form.elements[k].value=v;}
 const all=s.all_filters;
 fillSelect(form.elements.default_filter,all,s.settings.default_filter);
 $('enabledFilters').replaceChildren(...all.map(i=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=i.id;input.checked=s.settings.enabled_filters.includes(i.id);input.disabled=i.id==='none';label.append(input,document.createTextNode(i.label));return label;}));settingsLoaded=true;
}
async function refresh(){
 const requestedVersion=selectionVersion;
 try{
 const s=await api('/api/status');status=s;
 $('connection').textContent='● Đã kết nối';$('mode').textContent=s.skip_sensor?(s.demo?'DEMO / BỎ QUA HC-SR04':'BỎ QUA HC-SR04'):s.demo?'CAMERA MÔ PHỎNG':s.hardware?'GPIO + CAMERA THẬT':'CAMERA THẬT / GPIO MÔ PHỎNG';
 if(isAdmin){
 $('loginPanel').hidden=s.admin;$('adminPanel').hidden=!s.admin;
 if(s.admin){$('deviceError').hidden=!s.error;$('deviceError').textContent=s.error||'';$('start').disabled=s.state!=='stopped';$('stop').disabled=s.state==='stopped';$('adminState').textContent=states[s.state];$('distance').textContent=s.distance_m===null?'—':`${s.distance_m.toFixed(2)} m`;$('led').textContent=s.led?'● Đang sáng':'○ Đang tắt';$('disk').textContent=`${s.free_mb} MB`;$('simulation').hidden=s.hardware||s.skip_sensor;setupSettings(s);}
 }else{
 $('gestureHelp').hidden=!s.gesture_enabled;
 $('gestureHelp').textContent=s.gesture_error||(!s.owned?'Nhận quyền trên web, đứng giữa khung hình rồi giơ bàn tay mở để bật cử chỉ.':s.gesture_active?`Cử chỉ đang bật · Zoom ${s.zoom.toFixed(2)}×. 👍 Chụp · ✌ Hiệu ứng tiếp · ☝ Hiệu ứng trước · Nắm tay: hủy chụp. Co ba ngón cuối và mở/khép cái–trỏ để zoom.`:'Đứng giữa khung hình, giữ bàn tay mở để kích hoạt cử chỉ.');
 $('state').textContent=states[s.state];$('cameraLabel').textContent=s.demo?'DEMO':'CAM 01';
 const active=s.owned&&s.camera_ready&&['ready','capturing'].includes(s.state);
 $('preview').hidden=!active;$('placeholder').hidden=active;
 $('gesturePanel').hidden=!active||!s.gesture_enabled;
 const g=s.gesture_diagnostic||{};
 const label=gestureNames[g.label]||g.label||'Chưa xác định';
 $('gestureCurrent').textContent=s.gesture_error||(!g.message?'Đang chờ nhận diện cử chỉ…':`${g.message} · Mặt: ${g.faces} · Tay: ${g.hands} · ${g.zooming?'Đang zoom':label+' ('+Math.round((g.score||0)*100)+'%)'}`);
 const events=s.gesture_events||[];
 const logKey=JSON.stringify(events);
 if(logKey!==gestureLogKey){
  $('gestureLog').replaceChildren(...events.slice().reverse().map(event=>{
   const li=document.createElement('li');
   li.textContent=`${new Date(event.time).toLocaleTimeString('vi-VN')} · ${gestureNames[event.label]||event.label}: ${event.message}`;
   return li;
  }));
  gestureLogKey=logKey;
 }
 $('placeholderTitle').textContent=s.error?'Thiết bị cần kiểm tra':s.state==='stopped'?'Booth đang nghỉ':s.state==='waiting'?'Đến gần để bắt đầu':s.busy?'Vui lòng chờ lượt':'Booth đã sẵn sàng';
 $('placeholderText').textContent=s.error|| (s.state==='stopped'?'Admin cần nhấn Start để bật booth.':s.state==='waiting'?'Đứng trước cảm biến trong phạm vi được cài đặt.':s.busy?'Một người dùng khác đang sử dụng booth.':'Nhấn “Bắt đầu sử dụng” để xem camera và chọn hiệu ứng.');
 $('claim').disabled=s.state!=='ready'||s.busy||s.owned;$('claim').textContent=s.owned?'Bạn đang điều khiển':'Bắt đầu sử dụng';
 for(const id of ['filter','frame','capture','release'])$(id).disabled=!s.owned||s.state!=='ready';
 $('capture').disabled||=!s.camera_ready||selectionPending>0;
 $('countdown').hidden=s.state!=='capturing'||!s.owned;$('countdown').textContent=s.countdown||'📸';
 $('progress').hidden=s.state!=='capturing'||!s.owned;$('progress').textContent=`Đã chụp ${s.taken} / ${s.total}`;
 const key=JSON.stringify([s.filters,s.frames]);
 if(key!==optionsKey){
  $('effectChoices').replaceChildren(...s.filters.filter(f=>f.id!=='none').map(f=>{
   const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=f.id;input.name='effect';label.append(input,document.createTextNode(f.label));return label;
  }));
  fillSelect($('frame'),[{id:'none',label:'Không khung'},...s.frames.map(f=>({id:f,label:f.replace('.png','').replaceAll('_',' ')}))],s.frame);optionsKey=key;
 }
 if(s.owned&&selectionPending===0&&requestedVersion===selectionVersion){
  for(const input of $('effectChoices').querySelectorAll('input'))input.checked=(s.selected_filters||[]).includes(input.value);
  if(document.activeElement!==$('frame'))$('frame').value=s.frame;
 }
 const frame=$('frame').value;$('frameSample').hidden=frame==='none';if(frame!=='none')$('frameSample').src='/assets/frames/'+encodeURIComponent(frame);
 }
 if(s.state!==previousState){previousState=s.state;await loadSessions();}
 }catch(e){$('connection').textContent='○ Mất kết nối';if(!isAdmin){for(const id of ['claim','capture','filter','frame','release'])$(id).disabled=true;$('preview').hidden=true;$('placeholder').hidden=false;$('placeholderTitle').textContent='Mất kết nối với Pi';$('placeholderText').textContent='Kiểm tra Wi-Fi. Trang sẽ tự kết nối lại.';}if($('gesturePanel'))$('gesturePanel').hidden=true;status=null;}
}
async function loadSessions(force=false){
 if(isAdmin&&!status?.admin)return;
 const items=await api('/api/sessions');const key=JSON.stringify(items);if(key===sessionKey&&!force)return;sessionKey=key;
 const container=$(isAdmin?'adminSessions':'sessions');container.replaceChildren();
 if(!items.length){const p=document.createElement('p');p.className='muted';p.textContent='Chưa có ảnh. Các phiên chụp sẽ xuất hiện tại đây.';container.append(p);return;}
 for(const item of items){
 const section=document.createElement('section');section.className='session';
 const title=document.createElement('div');title.className='session-title';
 const text=document.createElement('span');text.textContent=`${new Date(item.created).toLocaleString('vi-VN')} · ${{complete:'Hoàn tất',capturing:'Đang chụp',failed:'Có lỗi',interrupted:'Đã dừng giữa chừng'}[item.status]||item.status}`;title.append(text);
 if(isAdmin){const del=document.createElement('button');del.className='ghost';del.textContent='Xóa phiên';del.disabled=item.status==='capturing';del.onclick=async()=>{if(!confirm('Xóa vĩnh viễn ảnh của phiên này?'))return;try{await api('/api/sessions/'+item.id,'DELETE');await loadSessions(true);}catch(e){notice(e.message);}};title.append(del);}
 section.append(title);const photos=document.createElement('div');photos.className='photos';
 for(const file of item.files){const figure=document.createElement('figure');figure.className='photo';const image=document.createElement('img');image.src='/media/'+encodeURIComponent(file);image.alt=file.endsWith('_strip.jpg')?'Ảnh ghép của phiên':'Ảnh trong phiên';image.loading='lazy';const link=document.createElement('a');link.href=image.src+'?download=1';link.textContent=file.endsWith('_strip.jpg')?'↓ Tải ảnh ghép':'↓ Tải ảnh';link.download=file;figure.append(image,link);photos.append(figure);}section.append(photos);container.append(section);
 }
}
async function previewLoop(){
 if(!isAdmin&&status?.owned&&status.camera_ready){
 try{const r=await fetch('/api/preview.jpg',{cache:'no-store'});if(r.ok&&r.status!==204){const next=URL.createObjectURL(await r.blob());$('preview').src=next;if(previewUrl)URL.revokeObjectURL(previewUrl);previewUrl=next;}}catch(e){/* status polling handles reconnect */}
 }setTimeout(previewLoop,160);
}
bind('claim',()=>api('/api/claim','POST',{}));bind('release',()=>api('/api/release','POST',{}));bind('capture',async()=>{$('capture').disabled=true;await selectionQueue;await api('/api/capture','POST',{});});
function saveSelection(){
 const data={filters:[...$('effectChoices').querySelectorAll('input:checked')].map(input=>input.value),frame:$('frame').value};
 selectionPending++;selectionVersion++;$('capture').disabled=true;
 $('frameSample').hidden=data.frame==='none';
 if(data.frame!=='none')$('frameSample').src='/assets/frames/'+encodeURIComponent(data.frame);
 selectionQueue=selectionQueue.then(async()=>{
  try{await api('/api/select','POST',data);}catch(e){notice(e.message);}
  finally{selectionPending--;}
 });
 selectionQueue.then(()=>refresh());
}
if($('effectChoices'))$('effectChoices').onchange=e=>{
 const input=e.target;
 if(input.checked&&['mono','sepia'].includes(input.value)){
  for(const other of $('effectChoices').querySelectorAll('input'))if(other!==input&&['mono','sepia'].includes(other.value))other.checked=false;
 }
 saveSelection();
};
if($('frame'))$('frame').onchange=saveSelection;
if($('clearEffects'))$('clearEffects').onclick=()=>{for(const input of $('effectChoices').querySelectorAll('input'))input.checked=false;saveSelection();};
bind('start',()=>api('/api/control','POST',{action:'start'}));bind('stop',()=>api('/api/control','POST',{action:'stop'}));bind('near',()=>api('/api/simulate','POST',{near:true}));bind('far',()=>api('/api/simulate','POST',{near:false}));bind('logout',async()=>{await api('/api/logout','POST',{});settingsLoaded=false;});bind('refreshSessions',()=>loadSessions(true));
if($('loginForm'))$('loginForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/login','POST',{password:$('password').value});$('password').value='';await refresh();await loadSessions(true);await loadEvents();}catch(err){notice(err.message);}};
if($('settingsForm'))$('settingsForm').onsubmit=async e=>{e.preventDefault();const form=e.target;const data={};for(const k of ['distance_m','absence_seconds','photo_count','countdown','retention_days'])data[k]=Number(form.elements[k].value);data.enabled_filters=[...$('enabledFilters').querySelectorAll('input:checked')].map(i=>i.value);data.default_filter=form.elements.default_filter.value;try{await api('/api/settings','POST',data);notice('Đã lưu cấu hình');}catch(err){notice(err.message);}};
async function loadEvents(){if(!isAdmin||!status?.admin)return;try{const events=await api('/api/events');$('events').replaceChildren(...events.map(e=>{const li=document.createElement('li'),time=document.createElement('time');time.textContent=new Date(e.created).toLocaleString('vi-VN');li.append(time,document.createTextNode(e.message));return li;}));}catch(e){notice(e.message);}}
async function poll(){await refresh();setTimeout(poll,800);}async function slowPoll(){try{await loadSessions();await loadEvents();}catch(e){/* retry next poll */}setTimeout(slowPoll,5000);}poll();previewLoop();slowPoll();
