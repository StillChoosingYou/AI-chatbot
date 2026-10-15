const messages=document.getElementById('messages');
let sending=false;
function append(text,kind) {const item=document.createElement('div');item.className=`message ${kind}`;item.textContent=text;messages.appendChild(item);messages.scrollTop=messages.scrollHeight;}
async function send(text) {
  if(sending)return;
  sending=true;append(text,'user');
  const buttons=[document.querySelector('#chat-form button'),...document.querySelectorAll('[data-question]')];
  for(const button of buttons)button.disabled=true;
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),20000);
  try {
    const response=await fetch('/api/chat',{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json','X-CSRF-Token':document.body.dataset.csrf},body:JSON.stringify({message:text})});
    const result=await response.json();append(result.reply || result.error || 'Unable to complete the request.','bot');
  } catch(error) {
    append('Unable to connect. Please try again.','bot');
    if(!document.getElementById('chat-input').value)document.getElementById('chat-input').value=text;
  } finally {clearTimeout(timeout);sending=false;for(const button of buttons)button.disabled=false;}
}
document.getElementById('chat-form').onsubmit=event=>{event.preventDefault();if(sending)return;const input=document.getElementById('chat-input');const text=input.value.trim();if(text){input.value='';send(text);}};
for(const button of document.querySelectorAll('[data-question]')) button.onclick=()=>send(button.dataset.question);
