let selected = null;
const studentRole = document.body.dataset.role === 'student';
let offerSignature = null, checkingOffer = false, bookingPending = false;
const $ = (id) => document.getElementById(id);
async function api(url, options = {}) {
  options.headers = {...options.headers, 'X-CSRF-Token': document.body.dataset.csrf};
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Unable to complete the request.');
  return body;
}
function notify(message) { $('toast').textContent = message; }
async function load() { if(studentRole) await refreshPickupOffer(); }
if (!studentRole) $('clear-selected').onclick=()=>{selected=null;$('selected-student').textContent='Ask by name or student number';};
function message(text,kind) { const node=document.createElement('div'); node.className=`message ${kind}`; node.textContent=text; $('messages').appendChild(node); $('messages').scrollTop=$('messages').scrollHeight; }
function retirePickupChoices() {
  for (const button of document.querySelectorAll('.pickup-options button')) button.disabled=true;
}
function showReply(response) {
  message(response.reply,'bot');
  if (response.action) reportForm(response.action);
  if (response.qr_url) {
    const card=document.createElement('div'); card.className='message bot chat-qr';
    const link=document.createElement('a'); link.href=response.scan_url; link.target='_blank'; link.rel='noopener';
    const image=document.createElement('img'); image.src=response.qr_url; image.alt=`Scan to view ${response.student_name}'s ID status`;
    link.appendChild(image); card.appendChild(link);
    const caption=document.createElement('p'); caption.textContent=`${response.student_name} · Scan to check ID status. Students must sign in to their own chatbot for personal details.`; card.appendChild(caption);
    $('messages').appendChild(card);
  }
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
  if (response.candidates) {
    const choices=document.createElement('div'); choices.className='message bot candidate-list';
    for (const row of response.candidates) {
      const button=document.createElement('button'); button.className='text-button';
      button.textContent=`${row.name} · ${row.student_id} · ${row.course}, ${row.section}, Year ${row.year_level}`;
      button.onclick=()=>{selected=row.id; $('selected-student').textContent=row.name;  send('What is the ID processing status?');}; choices.appendChild(button);
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
    const {offer}=await api('/api/pickup-offer');
     const signature=JSON.stringify(offer);
    if (signature!==offerSignature) {
      retirePickupChoices();
      offerSignature=signature;
      if (offer && offer.ready && !silent) showReply({reply:offer.reply,pickup_offer:offer});
    }
  } catch(error) { notify(error.message); }
  finally { checkingOffer=false; }
}
async function bookPickup(recordId,slotId) {
  if (bookingPending) return;
  bookingPending=true; retirePickupChoices();
  let succeeded=false;
  try {
    const response=await api(`/api/inventory/${recordId}/reschedule`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot_id:slotId})});
    succeeded=true; showReply(response); await refreshPickupOffer(true);
  } catch(error) {
    message(error.message,'bot');
    if (studentRole) offerSignature=null;
  } finally {
    bookingPending=false;
    await refreshPickupOffer(succeeded);
  }
}
async function send(text) {
  message(text,'user'); $('chat-form').querySelector('button').disabled=true;
  try {
    const response=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,record_id:selected})});
    if (studentRole && response.pickup_offer) offerSignature=JSON.stringify(response.pickup_offer);
    showReply(response);
  } catch(error) { message(error.message,'bot'); }
  finally { $('chat-form').querySelector('button').disabled=false; }
}
$('chat-form').onsubmit=event=>{ event.preventDefault(); const text=$('chat-input').value.trim(); if(text){$('chat-input').value='';send(text);} };
for(const button of document.querySelectorAll('[data-question]')) button.onclick=()=>send(button.dataset.question);
load();
function reportForm(action) {
  const lost=action==='report_lost';
  if (lost!==studentRole) return;
  const form=document.createElement('form'); form.className='message bot report-form';
  form.innerHTML=lost
    ? '<strong>Confirm your lost ID report</strong><label>Student number<input name="student_id" required maxlength="100"></label><label>Course<input name="course" required maxlength="100"></label><label>Section<input name="section" required maxlength="100"></label><label><input type="checkbox" name="confirmed" required> My ID is lost. Flag its QR and request replacement.</label><button class="primary">Confirm lost report</button><p role="alert"></p>'
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
if (!studentRole) {
  let cameraStream=null, cameraTimer=null;
  function stopCamera() {
    if (cameraTimer) clearTimeout(cameraTimer);
    cameraTimer=null;
    if (cameraStream) for (const track of cameraStream.getTracks()) track.stop();
    cameraStream=null; $('scanner-video').srcObject=null; $('scanner-video').hidden=true;
    $('stop-camera').hidden=true; $('start-camera').disabled=false;
  }
  async function openCode(code) {
    const result=await api(`/api/qr/lookup?code=${encodeURIComponent(code)}`);
    stopCamera(); location.assign(result.url);
  }
  $('scanner-form').onsubmit=async event=>{
    event.preventDefault();
    try { await openCode($('scan-code').value.trim()); }
    catch(error) { $('scanner-result').textContent=error.message; }
  };
  $('stop-camera').onclick=stopCamera;
  $('start-camera').onclick=async()=>{
    $('start-camera').disabled=true;
    try {
      if (!('BarcodeDetector' in window) || !(await BarcodeDetector.getSupportedFormats()).includes('qr_code')) throw new Error('Camera QR detection is unavailable in this browser. Scan with your phone camera and paste the code above.');
      cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});
      $('scanner-video').srcObject=cameraStream; $('scanner-video').hidden=false; $('stop-camera').hidden=false;
      await $('scanner-video').play();
      const detector=new BarcodeDetector({formats:['qr_code']});
      const scan=async()=>{
        if (!cameraStream) return;
        try {
          const codes=await detector.detect($('scanner-video'));
          if (!cameraStream) return;
          if (codes.length) { $('scan-code').value=codes[0].rawValue; await openCode(codes[0].rawValue); return; }
          cameraTimer=setTimeout(scan,300);
        } catch(error) { stopCamera(); $('scanner-result').textContent=error.message; }
      };
      cameraTimer=setTimeout(scan,300);
    } catch(error) { stopCamera(); $('scanner-result').textContent=error.message+' Camera access requires HTTPS or localhost.'; }
  };
  window.addEventListener('pagehide',stopCamera);
}

if(studentRole) {setInterval(()=>{if(document.visibilityState==='visible') refreshPickupOffer();},15000);document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible') refreshPickupOffer();});}
