let records = [], selected = null, editing = null, imageRecord = null;
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
async function load() {
  try {
    records = (await api('/api/inventory')).records;
    for (const [field, id, label] of [['course','course-filter','All courses'],['section','section-filter','All sections']]) {
      const previous = $(id).value;
      $(id).innerHTML = `<option value="">${label}</option>` + [...new Set(records.map(r => r[field]))].sort().map(v => `<option>${escapeText(v)}</option>`).join('');
      $(id).value = previous;
    }
    render();
  } catch (error) { notify(error.message); }
}
function render() {
  const groups = [['Total IDs', records.length, 'All student records'], ['Pending', records.filter(r=>r.status==='Pending').length, 'Awaiting requirements'], ['Ready for release',records.filter(r=>r.status==='Ready for release').length,'Available for pickup'], ['Released',records.filter(r=>r.status==='Released').length,'Successfully collected']];
  $('stats').innerHTML = groups.map(([label,total,detail],i)=>`<div class="stat"><div>${label}<span class="stat-symbol">${['▤','◷','✓','↗'][i]}</span></div><strong>${total.toString().padStart(2,'0')}</strong><small>${detail}</small></div>`).join('');
  const query = $('search').value.trim().toLowerCase();
  const shown = records.filter(r => `${r.name} ${r.student_id}`.toLowerCase().includes(query) && [['id_status','id-status-filter'],['status','status-filter'],['course','course-filter'],['year_level','year-filter'],['section','section-filter'],['request_type','kind-filter']].every(([field,id])=>!$(id).value || String(r[field])===$(id).value));
  $('count').textContent = `${shown.length} of ${records.length}`;
  $('records').innerHTML = shown.length ? shown.map(r=>`<article class="record ${selected===r.id?'selected':''}"><div class="student-side"><a href="/student/${encodeURIComponent(r.token)}" target="_blank" rel="noopener" class="qr" aria-label="Open student details for ${escapeText(r.name)}"><img src="/qr/${r.id}.png" alt="QR code for ${escapeText(r.student_id)}"></a><div><h3>${escapeText(r.name)}</h3><p>${escapeText(r.student_id)} <span class="divider">·</span> ${escapeText(r.request_type)}</p><div class="student-tags"><span>${escapeText(r.course)}</span><span>Section ${escapeText(r.section)}</span><span>Year ${r.year_level}</span></div><div class="record-actions"><a href="/sticker/${r.id}" target="_blank" rel="noopener">Print sticker ↗</a><button data-select="${r.id}">Ask about ID</button>${coordinator ? `<button data-images="${r.id}">ID images</button>` : ''}</div></div></div><div class="status-side"><p class="id-validity">ID status: <strong>${escapeText(r.id_status)}</strong></p><span class="status ${r.status.toLowerCase().replaceAll(' ','-')}"><span>●</span> ${escapeText(r.status)}</span><p class="appointment">${r.schedule_date ? `${escapeText(r.schedule_type)}<br><strong>${escapeText(r.schedule_date)} · ${escapeText(r.schedule_time)}</strong>` : 'No appointment scheduled'}</p>${r.status==='Pending' && r.pending_reason ? `<p class="reason">${escapeText(r.pending_reason)}</p>`:''}${coordinator ? `<button class="text-button" data-edit="${r.id}">Update / schedule ↗</button>` : ''}</div></article>`).join('') : '<div class="empty">No IDs match these filters.<br>Try another search or reset the filters.</div>';
}
for (const id of ['search','id-status-filter','status-filter','course-filter','year-filter','section-filter','kind-filter']) { $(id).addEventListener('input',render); $(id).addEventListener('change',render); }
$('reset-filters').onclick = () => { for (const id of ['search','id-status-filter','status-filter','course-filter','year-filter','section-filter','kind-filter']) $(id).value=''; render(); };
$('records').onclick = event => {
  const select = event.target.closest('[data-select]');
  const edit = event.target.closest('[data-edit]');
  const images = event.target.closest('[data-images]');
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
  $('record-dialog').showModal();
}
$('new-record').onclick=()=>openDialog(); $('close-dialog').onclick=()=>$('record-dialog').close();
$('record-form').onsubmit = async event => {
  event.preventDefault(); $('save-record').disabled=true;
  const body = Object.fromEntries(new FormData(event.target));
  try { await api(editing ? `/api/inventory/${editing}` : '/api/inventory',{method:editing?'PATCH':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); $('record-dialog').close(); await load(); notify('Record saved.'); }
  catch(error) { $('form-error').textContent=error.message; }
  finally { $('save-record').disabled=false; }
};
function message(text,kind) { const node=document.createElement('div'); node.className=`message ${kind}`; node.textContent=text; $('messages').appendChild(node); $('messages').scrollTop=$('messages').scrollHeight; }
async function send(text) {
  message(text,'user'); $('chat-form').querySelector('button').disabled=true;
  try { const response=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,record_id:selected})}); message(response.reply,'bot');
    if (response.qr_url) {
      const card = document.createElement('div'); card.className='message bot chat-qr';
      const link = document.createElement('a'); link.href=response.scan_url; link.target='_blank'; link.rel='noopener';
      const image = document.createElement('img'); image.src=response.qr_url; image.alt=`Scan to view ${response.student_name}'s ID status`;
      link.appendChild(image); card.appendChild(link);
      const caption=document.createElement('p'); caption.textContent=`${response.student_name} · Scan or open to view current status and ID images.`; card.appendChild(caption);
      $('messages').appendChild(card);
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
    $('messages').scrollTop=$('messages').scrollHeight; }
  catch(error) { message(error.message,'bot'); }
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
