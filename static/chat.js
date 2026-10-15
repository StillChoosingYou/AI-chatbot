const studentRole = document.body.dataset.role === 'student';
let offerSignature = null, checkingOffer = false, bookingPending = false, sending = false;
const $ = (id) => document.getElementById(id);
async function api(url, options = {}) {
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),20000);
  try {
    const response=await fetch(url,{...options,signal:controller.signal,headers:{...options.headers,'X-CSRF-Token':document.body.dataset.csrf}});
    const body=await response.json();
    if (!response.ok) throw new Error(response.status===401 ? 'Your session has expired. Sign in again.' : body.error || 'Unable to complete the request.');
    return body;
  } catch(error) {
    if (error.name==='AbortError') throw new Error('The request timed out. Check your connection and try again.');
    if (error instanceof TypeError) throw new Error('Unable to connect. Check your connection and try again.');
    throw error;
  } finally { clearTimeout(timeout); }
}
function notify(message) { $('toast').textContent = message; }
async function load() { if(studentRole) await refreshPickupOffer(); }
function message(text,kind) { const node=document.createElement('div'); node.className=`message ${kind}`; node.textContent=text; $('messages').appendChild(node); $('messages').scrollTop=$('messages').scrollHeight; }
function retirePickupChoices() {
  for (const button of document.querySelectorAll('.pickup-options button')) button.disabled=true;
}
function showReply(response) {
  message(response.reply,'bot');
  if (response.action==='scan_id' && !studentRole) {
    $('id-scanner').scrollIntoView({behavior:'smooth',block:'start'});
    $('scan-code').focus({preventScroll:true});
  } else if (response.action) reportForm(response.action);
  if (studentRole && response.scan_url && !response.qr_url) {
    const link=document.createElement('a');link.href=response.scan_url;link.target='_blank';link.rel='noopener';link.textContent='Open my ID details';link.className='message bot';$('messages').appendChild(link);
  }
  if (response.pickup_offer && response.pickup_offer.ready && response.pickup_offer.slots.length) {
    retirePickupChoices();
    const offer=response.pickup_offer;
    const choices=document.createElement('div'); choices.className='message bot pickup-options';
    for (const slot of offer.slots) {
      const button=document.createElement('button'); button.className='pickup-choice';
      button.textContent=`${slot.current ? 'Currently booked' : 'Book'} · ${slot.schedule_date} at ${slot.schedule_time} (UTC+8)`;
      button.disabled=slot.current || !offer.can_book;
      button.onclick=()=>bookPickup(offer.record_id,slot.id);
      choices.appendChild(button);
    }
    $('messages').appendChild(choices);
  }
  $('messages').scrollTop=$('messages').scrollHeight;
}
async function refreshPickupOffer(silent=false) {
  if (!studentRole || checkingOffer || bookingPending) return;
  checkingOffer=true;
  try {
    await receiveNotifications();
    const {offer,record}=await api('/api/pickup-offer');
    await renderStudentProgress(record);
    const signature=JSON.stringify(offer);
    if (signature!==offerSignature) {
      retirePickupChoices();
      offerSignature=signature;
      if (offer && offer.ready && !silent) showReply({reply:offer.reply,pickup_offer:offer});
    }
  } catch(error) { notify(error.message); $('progress-error').textContent='Unable to refresh your progress. Tap Refresh to try again.'; }
  finally { checkingOffer=false; }
}
async function bookPickup(recordId,slotId) {
  if (bookingPending) return;
  bookingPending=true; retirePickupChoices();
  try {
    const response=await api(`/api/inventory/${recordId}/reschedule`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot_id:slotId})});
    showReply(response); offerSignature=null;
  } catch(error) {
    message(error.message,'bot');
    if (studentRole) offerSignature=null;
  } finally {
    bookingPending=false;
    await refreshPickupOffer();
  }
}
function chatBusy(busy) {
  sending=busy;
  $('chat-form').querySelector('button').disabled=busy;
  for (const button of document.querySelectorAll('[data-question]')) button.disabled=busy;
  $('chat-status').textContent=busy ? 'Checking your request…' : '';
  $('messages').setAttribute('aria-busy',String(busy));
}
async function send(text) {
  if (sending) return;
  message(text,'user'); chatBusy(true);
  try {
    const response=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    if (studentRole && response.pickup_offer) offerSignature=JSON.stringify(response.pickup_offer);
    showReply(response);
  } catch(error) {
    message(error.message,'bot');
    const button=document.createElement('button');button.className='text-button retry-message';button.textContent='Try this question again';
    button.onclick=()=>{if(!sending){button.remove();send(text);}};
    $('messages').appendChild(button);
    $('messages').scrollTop=$('messages').scrollHeight;
  } finally { chatBusy(false); }
}
$('chat-form').onsubmit=event=>{ event.preventDefault(); if(sending)return; const text=$('chat-input').value.trim(); if(text){$('chat-input').value='';send(text);} };
for(const button of document.querySelectorAll('[data-question]')) button.onclick=()=>send(button.dataset.question);
$('start-over').onclick=()=>{
  if (sending || bookingPending || document.querySelector('.report-form button:disabled')) {notify('Wait for your current request to finish.');return;}
  $('messages').replaceChildren();
  message(studentRole ? 'New conversation. Ask about your ID or choose a quick question below.' : 'New conversation. Scan an ID QR code to check its status.','bot');
  $('chat-input').value='';$('chat-input').focus();offerSignature=null;
  if (studentRole) refreshPickupOffer();
};
if (studentRole) $('refresh-progress').onclick=()=>refreshPickupOffer();
load();
function reportForm(action) {
  const lost=action==='report_lost';
  if (lost!==studentRole) return;
  const form=document.createElement('form'); form.className='message bot report-form';
  form.innerHTML=lost
    ? '<strong>Confirm your lost ID report</strong><label>Student number<input name="student_id" required maxlength="100"></label><label>Course<input name="course" required maxlength="100"></label><label>Section<input name="section" required maxlength="100"></label><label><input type="checkbox" name="confirmed" required> My ID is lost. Revoke its old QR and request replacement.</label><button class="primary">Confirm lost report</button><p role="alert"></p>'
    : '<strong>Log a physically received ID</strong><label>Scanned QR code<input name="code" required maxlength="500"></label><label><input type="checkbox" name="confirmed" required> Security has physically received this ID.</label><button class="primary">Confirm found report</button><p role="alert"></p>';
  form.onsubmit=async event=>{
    event.preventDefault(); const button=form.querySelector('button'); button.disabled=true;
    const body=Object.fromEntries(new FormData(form)); body.confirmed=form.elements.confirmed.checked;
    try {
      const result=await api(`/api/id-reports/${lost ? 'lost' : 'found'}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      showReply(result); form.remove(); offerSignature=null; await refreshPickupOffer();
    } catch(error) { form.querySelector('[role=alert]').textContent=error.message; button.disabled=false; }
  };
  $('messages').appendChild(form); $('messages').scrollTop=$('messages').scrollHeight;
}
const displayedNotices=new Set();
async function receiveNotifications() {
  const {notifications}=await api('/api/notifications');
  for (const item of notifications) if (!displayedNotices.has(item.id)) {
    message(item.message,'bot'); displayedNotices.add(item.id);
  }
  if (notifications.length) await api('/api/notifications/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:notifications.map(item=>item.id)})});
}

if(studentRole) {setInterval(()=>{if(document.visibilityState==='visible') refreshPickupOffer();},15000);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible') refreshPickupOffer();});}

async function renderStudentProgress(record) {
  const summary=$('progress-summary');summary.replaceChildren();
  const name=document.createElement('strong');name.textContent=record.name;summary.appendChild(name);
  for(const text of [`ID: ${record.id_status}`,`Processing: ${record.status}`]) {
    const badge=document.createElement('span');badge.className='progress-badge';badge.textContent=text;summary.appendChild(badge);
  }
  if(record.pending_reason){const reason=document.createElement('p');reason.textContent=`Pending requirement: ${record.pending_reason}`;summary.appendChild(reason);}
  const stages=['Pending',record.status==='Replacement Processing' ? 'Replacement Processing' : 'Processing','Ready for release','Released'];
  const current=record.status==='Unclaimed' ? 2 : stages.indexOf(record.status);
  $('progress-steps').replaceChildren();
  if(current>=0 && !['Found - For Claiming','Missing from Stock'].includes(record.id_status)) stages.forEach((stage,index)=>{
    const step=document.createElement('li');step.textContent=stage;step.className=index<current ? 'complete' : index===current ? 'current' : '';
    if(index===current)step.setAttribute('aria-current','step');$('progress-steps').appendChild(step);
  });
  $('my-appointment').textContent=record.schedule_date ? `${record.schedule_type} · ${record.schedule_date} at ${record.schedule_time} (UTC+8)` : 'No appointment booked. Ask the assistant for available dates.';
  $('download-calendar').hidden=!record.schedule_date;
  $('progress-error').textContent='';
  try {
    const {tickets}=await api('/api/id-tickets');
    $('ticket-count').textContent=`(${tickets.length})`;$('my-tickets').replaceChildren();
    if(!tickets.length)$('my-tickets').textContent='No reports yet.';
    for(const ticket of tickets) {
      const item=document.createElement('article');item.className='ticket-item';
      const title=document.createElement('strong');title.textContent=ticket.reference;
      const detail=document.createElement('p');detail.textContent=`${ticket.kind} · ${ticket.state} · ${ticket.created_at} (UTC+8)`;
      item.append(title,detail);$('my-tickets').appendChild(item);
    }
  } catch(error) {$('my-tickets').textContent='Unable to refresh reports. Tap Refresh to try again.';}
}
function initVoiceTyping() {
  const Recognition=window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!Recognition || !window.isSecureContext){
    $('voice-note').hidden=false;
    $('voice-note').textContent='Voice typing requires HTTPS or localhost and a supported browser. You can always type your question.';
    return;
  }
  const recognition=new Recognition();let listening=false;
  $('voice-input').hidden=false;$('voice-language').hidden=false;$('voice-note').hidden=false;
  recognition.interimResults=false;recognition.maxAlternatives=1;
  $('voice-input').onclick=()=>{
    if(listening){recognition.stop();return;}
    recognition.lang=$('voice-language').value;
    try{recognition.start();}catch(error){$('voice-status').textContent='Voice typing could not start. Please type your question.';}
  };
  recognition.onstart=()=>{listening=true;$('voice-input').textContent='Stop voice typing';$('voice-status').textContent='Listening…';};
  recognition.onresult=event=>{
    const text=event.results[0][0].transcript;
    $('chat-input').value=($('chat-input').value+' '+text).trim().slice(0,1000);
    $('voice-status').textContent='Review the text, then tap Send.';$('chat-input').focus();
  };
  recognition.onerror=()=>{$('voice-status').textContent='Voice typing unavailable. Check microphone permission or type your question.';};
  recognition.onend=()=>{listening=false;$('voice-input').textContent='🎙 Voice typing';};
  window.addEventListener('pagehide',()=>recognition.abort());
}
initVoiceTyping();
