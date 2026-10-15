for (const [id, endpoint, field] of [['found-form','/api/id-reports/found','confirmed'],['claim-form',null,'verified']]) {
  const form=document.getElementById(id);
  if (!form) continue;
  form.onsubmit=async event=>{
    event.preventDefault();
    const button=form.querySelector('button'); button.disabled=true;
    const body=id==='found-form' ? {code:form.dataset.code,confirmed:form.elements[field].checked} : {verified:form.elements[field].checked};
    try {
      const response=await fetch(endpoint || `/api/inventory/${form.dataset.record}/claim`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':document.body.dataset.csrf},body:JSON.stringify(body)});
      const result=await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to save.');
      document.getElementById('scan-result').textContent=result.reply;
      const refresh=document.createElement('a'); refresh.href=location.href; refresh.textContent=' Refresh status'; document.getElementById('scan-result').appendChild(refresh);
    } catch(error) { document.getElementById('scan-result').textContent=error.message; button.disabled=false; }
  };
}
