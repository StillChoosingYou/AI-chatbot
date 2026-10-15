let records = [], selected = null, editing = null, imageRecord = null, rescheduling = null;
const studentRole = document.body.dataset.role === 'student';
let offerSignature = null, checkingOffer = false, bookingPending = false;
const coordinator = document.body.dataset.role === 'coordinator';
const $ = (id) => document.getElementById(id);
const escapeText = (value) => String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
async function api(url, options = {}) {
  options.headers = {...options.headers, 'X-CSRF-Token': document.body.dataset.csrf};
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Unable to complete the request.');
  return body;
}
function notify(message) { $('toast').textContent = message; }
async function load(silentOffer=false) {
  try {
    records = (await api('/api/inventory')).records;
    for (const [field, id, label] of [['course','course-filter','All courses'],['section','section-filter','All sections']]) {
      const previous = $(id).value;
      $(id).innerHTML = `<option value="">${label}</option>` + [...new Set(records.map(r => r[field]))].sort().map(v => `<option>${escapeText(v)}</option>`).join('');
      $(id).value = previous;
    }
    render();
    if (studentRole) await refreshPickupOffer(silentOffer);
    if (coordinator) await loadPickupSlots();
    await loadTickets();
  } catch (error) { notify(error.message); }
}
function render() {
  const groups = [['Total IDs', records.length, 'All student records'], ['Pending', records.filter(r=>r.status==='Pending').length, 'Awaiting requirements'], ['Ready for release',records.filter(r=>r.status==='Ready for release').length,'Available for pickup'], ['Released',records.filter(r=>r.status==='Released').length,'Successfully collected']];
  $('stats').innerHTML = groups.map(([label,total,detail],i)=>`<div class="stat"><div>${label}<span class="stat-symbol">${['▤','◷','✓','↗'][i]}</span></div><strong>${total.toString().padStart(2,'0')}</strong><small>${detail}</small></div>`).join('');
  const query = $('search').value.trim().toLowerCase();
  const shown = records.filter(r => `${r.name} ${r.student_id}`.toLowerCase().includes(query) && [['id_status','id-status-filter'],['status','status-filter'],['course','course-filter'],['year_level','year-filter'],['section','section-filter'],['request_type','kind-filter']].every(([field,id])=>!$(id).value || String(r[field])===$(id).value));
  $('count').textContent = `${shown.length} of ${records.length}`;
  $('records').innerHTML = shown.length ? shown.map(r=>`<article class="record ${selected===r.id?'selected':''}"><div class="student-side"><a href="${studentRole ? `/api/inventory/${r.id}/view` : `/student/${encodeURIComponent(r.token)}`}" target="_blank" rel="noopener" class="qr" aria-label="Open student details for ${escapeText(r.name)}">${studentRole ? '<span class="own-id-link">View ID</span>' : `<img src="/qr/${r.id}.png" alt="School ID QR code">`}</a><div><h3>${escapeText(r.name)}</h3><p>${escapeText(r.student_id)} <span class="divider">·</span> ${escapeText(r.request_type)}</p><div class="student-tags"><span>${escapeText(r.course)}</span><span>Section ${escapeText(r.section)}</span><span>Year ${r.year_level}</span></div><div class="record-actions">${!studentRole ? `<a href="/sticker/${r.id}" target="_blank" rel="noopener">Print sticker ↗</a>` : ''}${studentRole ? `<button data-select="${r.id}">Ask about ID</button>` : ''}${coordinator ? `<button data-images="${r.id}">ID images</button>` : ''}</div></div></div><div class="status-side"><p class="id-validity">ID status: <strong>${escapeText(r.id_status)}</strong></p><span class="status ${r.status.toLowerCase().replaceAll(' ','-')}"><span>●</span> ${escapeText(r.status)}</span><p class="appointment">${r.schedule_date ? `${escapeText(r.schedule_type)}<br><strong>${escapeText(r.schedule_date)} · ${escapeText(r.schedule_time)}</strong>` : 'No appointment scheduled'}</p>${r.status==='Pending' && r.pending_reason ? `<p class="reason">${escapeText(r.pending_reason)}</p>`:''}${['Lost','Reported Lost'].includes(r.id_status) ? `<p class="replacement-availability">${['Ready for release','Unclaimed'].includes(r.status) ? 'Replacement available for pickup' : 'Replacement not yet available for pickup'}</p>${coordinator || studentRole ? `<button class="text-button replacement-action" data-reschedule="${r.id}" ${!['Pending','Ready for release','Unclaimed'].includes(r.status) ? 'disabled' : ''}>${studentRole ? 'Choose / change appointment' : 'Schedule / reschedule replacement'}</button>` : ''}` : ''}${coordinator && ['Ready for release','Unclaimed'].includes(r.status) ? `<button class="text-button" data-audit="${r.id}">Report missing stock</button>` : ''}${coordinator ? `<button class="text-button" data-edit="${r.id}">Update / schedule ↗</button>` : ''}</div></article>`).join('') : '<div class="empty">No IDs match these filters.<br>Try another search or reset the filters.</div>';
}
for (const id of ['search','id-status-filter','status-filter','course-filter','year-filter','section-filter','kind-filter']) { $(id).addEventListener('input',render); $(id).addEventListener('change',render); }
$('reset-filters').onclick = () => { for (const id of ['search','id-status-filter','status-filter','course-filter','year-filter','section-filter','kind-filter']) $(id).value=''; render(); };
$('records').onclick = event => {
  const audit=event.target.closest('[data-audit]');
  if (audit) auditStock(Number(audit.dataset.audit));
  const select = event.target.closest('[data-select]');
  const edit = event.target.closest('[data-edit]');
  const images = event.target.closest('[data-images]');
  const reschedule = event.target.closest('[data-reschedule]');
  if (reschedule) {
    const row=records.find(r=>r.id===Number(reschedule.dataset.reschedule));
    if (studentRole) { selected=row.id; send('Can I reschedule my lost ID pickup?'); }
    else openReschedule(row);
  }
  if (images) openImages(records.find(r=>r.id===Number(images.dataset.images)));
  if (select) { selected=Number(select.dataset.select); $('selected-student').textContent=records.find(r=>r.id===selected).name; render(); $('chat-input').focus(); }
  if (edit) openDialog(records.find(r=>r.id===Number(edit.dataset.edit)));
};
$('clear-selected').onclick = () => { selected=null; $('selected-student').textContent='No student selected'; render(); };
function openDialog(row=null) {
  editing=row ? row.id : null;
  $('record-form').reset(); $('form-error').textContent='';
  $('dialog-title').textContent=row ? `Update ${row.name}` : 'Add student ID';
  $('identity-fields').hidden=!!row; $('processing-fields').hidden=!row; $('schedule-hint').hidden=!row;
  for (const input of $('identity-fields').querySelectorAll('input,select')) input.disabled=!!row;
  for (const input of $('processing-fields').querySelectorAll('input,select,textarea')) input.disabled=!row;
  if (row) for (const key of ['id_status','status','schedule_type','schedule_date','schedule_time','pending_reason']) $('record-form').elements[key].value=row[key];
  for (const option of $('record-form').elements.id_status.options) option.disabled=['Reported Lost','Found - For Claiming','Missing from Stock'].includes(option.value) && option.value!==row?.id_status;
  $('record-dialog').showModal();
}
$('new-record').onclick=()=>openDialog(); $('close-dialog').onclick=()=>$('record-dialog').close();
$('record-form').onsubmit = async event => {
  event.preventDefault(); $('save-record').disabled=true;
  const body = Object.fromEntries(new FormData(event.target));
  const original=records.find(row=>row.id===editing);
  if (original && ['Lost','Reported Lost'].includes(original.id_status) && body.status==='Released' && !confirm('Confirm actual collection: issue the replacement with a NEW QR and permanently deactivate the original QR? Print the new sticker after saving.')) { $('save-record').disabled=false; return; }
  try { await api(editing ? `/api/inventory/${editing}` : '/api/inventory',{method:editing?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); $('record-dialog').close(); await load(); notify('Record saved.'); }
  catch(error) { $('form-error').textContent=error.message; }
  finally { $('save-record').disabled=false; }
};
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
    const caption=document.createElement('p'); caption.textContent=`${response.student_name} · Open your ID workspace, or ask signed-in staff to scan the code.`; card.appendChild(caption);
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
      button.onclick=()=>{selected=row.id; $('selected-student').textContent=row.name; render(); send('What is the ID processing status?');}; choices.appendChild(button);
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
    records=records.map(row=>row.id===record.id ? {...row,...record} : row); render();
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
    succeeded=true; showReply(response); await load(true);
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

function refreshImagePreviews() {
  for (const side of ['front','back']) {
    $(`${side}-preview`).src=`/student/${encodeURIComponent(imageRecord.token)}/id/${side}.png?updated=${Date.now()}`;
    $(`${side}-state`).textContent=imageRecord[`has_${side}_image`] ? 'Uploaded image' : 'Sample placeholder';
    $(`remove-${side}`).hidden=!imageRecord[`has_${side}_image`];
  }
}
function openImages(row) {
  imageRecord=row;
  $('images-form').reset(); $('images-error').textContent='';
  $('images-title').textContent=`ID images · ${row.name}`;
  refreshImagePreviews(); $('images-dialog').showModal();
}
$('close-images').onclick=()=>$('images-dialog').close();
$('images-form').onsubmit=async event=>{
  event.preventDefault(); $('images-error').textContent='';
  const body=new FormData();
  for (const side of ['front','back']) {
    const file=event.target.elements[side].files[0];
    if(file) {
      if(file.size>5*1024*1024) { $('images-error').textContent='Each image must be at most 5 MB.'; return; }
      body.append(side,file);
    }
  }
  if (![...body.keys()].length) { $('images-error').textContent='Choose a front or back image to upload.'; return; }
  const targetId=imageRecord.id;
  $('save-images').disabled=true;
  try { await api(`/api/inventory/${targetId}/images`,{method:'POST',body}); await load(); $('images-dialog').close(); notify('ID images saved. Scan the QR code to view both sides.'); }
  catch(error) { $('images-error').textContent=error.message; }
  finally { $('save-images').disabled=false; }
};
for(const side of ['front','back']) $(`remove-${side}`).onclick=async()=>{
  const targetId=imageRecord.id; $(`remove-${side}`).disabled=true;
  try { await api(`/api/inventory/${targetId}/images/${side}`,{method:'DELETE'}); await load(); imageRecord=records.find(r=>r.id===targetId); refreshImagePreviews(); notify('Uploaded image removed. Sample placeholder restored.'); }
  catch(error) { $('images-error').textContent=error.message; }
  finally { $(`remove-${side}`).disabled=false; }
};


async function openReschedule(row) {
  rescheduling=row.id;
  $('reschedule-form').reset(); $('reschedule-error').textContent='';
  $('reschedule-title').textContent=`Replacement pickup · ${row.name}`;
  $('previous-appointment').textContent=row.schedule_date
    ? `Current appointment: ${row.schedule_type} · ${row.schedule_date} at ${row.schedule_time}`
    : 'No appointment is currently recorded.';
  $('reschedule-slots').innerHTML='<option value="">Loading available dates…</option>';
  $('save-reschedule').disabled=true; $('reschedule-dialog').showModal();
  try {
    const {offer}=await api(`/api/inventory/${row.id}/pickup-slots`);
    $('reschedule-slots').innerHTML='<option value="">Choose a published date and time</option>'+offer.slots.map(slot=>`<option value="${slot.id}">${slot.schedule_date} · ${slot.schedule_time}${slot.current ? ' · Current appointment' : ''}</option>`).join('');
    if (!offer.slots.length) $('reschedule-error').textContent='No dates are available. Publish a pickup slot first.';
    $('save-reschedule').disabled=!offer.slots.length;
  } catch(error) { $('reschedule-error').textContent=error.message; }
}
$('close-reschedule').onclick=()=>$('reschedule-dialog').close();
$('reschedule-form').onsubmit=async event=>{
  event.preventDefault(); $('save-reschedule').disabled=true; $('reschedule-error').textContent='';
  try {
    const response=await api(`/api/inventory/${rescheduling}/reschedule`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot_id:Number($('reschedule-slots').value)})});
    $('reschedule-dialog').close(); await load(); notify(response.reply);
  } catch(error) { $('reschedule-error').textContent=error.message; }
  finally { $('save-reschedule').disabled=false; }
};
async function loadPickupSlots() {
  const {slots}=await api('/api/pickup-slots');
  $('published-slots').innerHTML=slots.length ? slots.map(slot=>`<article class="published-slot"><span><strong>${escapeText(slot.purpose)} · ${slot.schedule_date} · ${slot.schedule_time}</strong> (UTC+8)<br>${slot.booked} / ${slot.capacity} students booked · ${slot.expired ? 'Past' : slot.enabled ? 'Open' : 'Closed'}</span>${!slot.expired ? `<button class="text-button" data-slot="${slot.id}" data-enabled="${slot.enabled ? 'false' : 'true'}">${slot.enabled ? 'Close' : 'Reopen'}</button>` : ''}</article>`).join('') : '<p>No pickup dates published yet.</p>';
}
if (coordinator) {
  $('slot-form').onsubmit=async event=>{
    event.preventDefault(); $('publish-slot').disabled=true; $('slot-error').textContent='';
    const body=Object.fromEntries(new FormData(event.target)); body.capacity=Number(body.capacity);
    try {
      await api('/api/pickup-slots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      event.target.reset(); await loadPickupSlots(); notify('Appointment published. Eligible students will see it in the chatbot.');
    } catch(error) { $('slot-error').textContent=error.message; }
    finally { $('publish-slot').disabled=false; }
  };
  $('published-slots').onclick=async event=>{
    const button=event.target.closest('[data-slot]'); if (!button) return;
    button.disabled=true;
    try {
      await api(`/api/pickup-slots/${button.dataset.slot}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:button.dataset.enabled==='true'})});
      await loadPickupSlots();
    } catch(error) { notify(error.message); button.disabled=false; }
  };
}
if (studentRole) {
  setInterval(()=>{if(document.visibilityState==='visible') refreshPickupOffer();},15000);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible') refreshPickupOffer();});
}

if (coordinator) setInterval(()=>{if(document.visibilityState==='visible') loadPickupSlots().catch(error=>notify(error.message));},15000);

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
      showReply(result); form.remove(); offerSignature=null; await load();
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
async function loadTickets() {
  const {tickets}=await api('/api/id-tickets');
  $('tickets').innerHTML=tickets.length ? tickets.map(ticket=>`<p><strong>${escapeText(ticket.reference)}</strong> · ${escapeText(records.find(row=>row.id===ticket.record_id)?.name || `Record ${ticket.record_id}`)} · ${escapeText(ticket.kind)} · ${escapeText(ticket.state)} · ${escapeText(ticket.created_at)} (UTC+8)${coordinator && ticket.kind==='found' && !ticket.owner_notified_at && ['For claiming','Revoked ID held','Retired ID held'].includes(ticket.state) ? `<br><button class="text-button" data-notify-owner="${ticket.id}">PMO: notify owner</button>` : ticket.kind==='found' ? `<br>${ticket.owner_notified_at ? 'PMO notified owner' : 'PMO handles owner notification'}` : ''}</p>`).join('') : '<p>No reports recorded.</p>';
}
$('refresh-tickets').onclick=()=>loadTickets().catch(error=>notify(error.message));
async function auditStock(id) {
  if (!confirm('Confirm: you physically checked office stock and this ready ID is missing?')) return;
  try {
    const result=await api(`/api/inventory/${id}/audit`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true})});
    notify(result.reply); await load();
  } catch(error) { notify(error.message); }
}
if (coordinator) {
  api('/api/id-policy').then(policy=>{for (const key of ['requirements','fee','claim_location']) $('policy-form').elements[key].value=policy[key];}).catch(error=>notify(error.message));
  $('policy-form').onsubmit=async event=>{
    event.preventDefault(); const button=event.target.querySelector('button'); button.disabled=true;
    try {
      await api('/api/id-policy',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});
      $('policy-result').textContent='Approved policy saved.';
    } catch(error) { $('policy-result').textContent=error.message; }
    finally { button.disabled=false; }
  };
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

$('tickets').onclick=async event=>{
  const button=event.target.closest('[data-notify-owner]'); if(!button) return;
  if(!confirm('PMO: confirm the physical ID is in custody and notify its owner?')) return;
  button.disabled=true;
  try {
    const result=await api(`/api/id-tickets/${button.dataset.notifyOwner}/notify-owner`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmed:true})});
    notify(result.reply);await loadTickets();
  } catch(error) {notify(error.message);button.disabled=false;}
};
