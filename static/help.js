const messages=document.getElementById('messages');
function append(text,kind) {const item=document.createElement('div');item.className=`message ${kind}`;item.textContent=text;messages.appendChild(item);messages.scrollTop=messages.scrollHeight;}
async function send(text) {
  append(text,'user');const button=document.querySelector('#chat-form button');button.disabled=true;
  try {
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.body.dataset.csrf},body:JSON.stringify({message:text})});
    const result=await response.json();append(result.reply || result.error || 'Unable to complete the request.','bot');
  } catch(error) {append('Unable to connect. Please try again.','bot');}
  finally {button.disabled=false;}
}
document.getElementById('chat-form').onsubmit=event=>{event.preventDefault();const input=document.getElementById('chat-input');const text=input.value.trim();if(text){input.value='';send(text);}};
for(const button of document.querySelectorAll('[data-question]')) button.onclick=()=>send(button.dataset.question);
