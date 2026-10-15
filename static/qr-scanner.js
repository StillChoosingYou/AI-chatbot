/* Shared PMO/guard scanner. Photos and camera frames stay on this device. */
(()=>{

  let cameraStream=null, cameraTimer=null, scanPending=false, cameraGeneration=0;
  const canvas=document.createElement('canvas');
  const context=canvas.getContext('2d',{willReadFrequently:true});
  function decodeQR(source,width,height) {
    if(!width || !height) return null;
    const scale=Math.min(1,1600/Math.max(width,height));
    canvas.width=Math.max(1,Math.round(width*scale));canvas.height=Math.max(1,Math.round(height*scale));
    context.drawImage(source,0,0,canvas.width,canvas.height);
    const pixels=context.getImageData(0,0,canvas.width,canvas.height);
    return jsQR(pixels.data,pixels.width,pixels.height,{inversionAttempts:'attemptBoth'})?.data || null;
  }
  function stopCamera() {
    cameraGeneration++;
    if (cameraTimer) clearTimeout(cameraTimer);
    cameraTimer=null;
    if (cameraStream) for (const track of cameraStream.getTracks()) track.stop();
    cameraStream=null; $('scanner-video').srcObject=null; $('scanner-video').hidden=true;
    $('stop-camera').hidden=true; $('start-camera').disabled=scanPending || !cameraAvailable;
  }
  async function openCode(code) {
    if(scanPending) return;
    scanPending=true;stopCamera();
    $('scanner-form').querySelector('.primary').disabled=true;
    $('scanner-result').textContent='Checking the scanned code…';
    try {
      const result=await api(`/api/qr/lookup?code=${encodeURIComponent(code)}`);
      location.assign(result.url);
    } catch(error) {
      $('scanner-result').textContent=error.message+' Check the QR code and try again.';
    } finally {
      scanPending=false;$('start-camera').disabled=!cameraAvailable;
      $('scanner-form').querySelector('.primary').disabled=false;
    }
  }
  const cameraAvailable=window.isSecureContext && Boolean(navigator.mediaDevices?.getUserMedia);
  if(!cameraAvailable) {
    $('start-camera').disabled=true;
    $('start-camera').title='Camera requires HTTPS or localhost and a supported browser.';
    $('scanner-result').textContent='Camera requires HTTPS or localhost. Upload a QR photo or paste a scanned code instead.';
  }
  $('scanner-form').onsubmit=event=>{event.preventDefault();openCode($('scan-code').value.trim());};
  $('stop-camera').onclick=()=>{stopCamera();$('scanner-result').textContent='Camera stopped.';};
  $('start-camera').onclick=async()=>{
    const generation=++cameraGeneration;
    $('start-camera').disabled=true;
    $('scanner-result').textContent='Allow camera access, then hold the QR code steady.';
    try {
      const stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});
      if(generation!==cameraGeneration){for(const track of stream.getTracks())track.stop();return;}
      cameraStream=stream;
      $('scanner-video').srcObject=cameraStream; $('scanner-video').hidden=false; $('stop-camera').hidden=false;
      await $('scanner-video').play();
      const scan=()=>{
        if (!cameraStream) return;
        try {
          const video=$('scanner-video');const code=decodeQR(video,video.videoWidth,video.videoHeight);
          if(code){$('scan-code').value=code;openCode(code);return;}
          cameraTimer=setTimeout(scan,300);
        } catch(error) {stopCamera();$('scanner-result').textContent='Could not read the camera. Upload a QR photo or paste the scanned code.';}
      };
      cameraTimer=setTimeout(scan,300);
    } catch(error) {stopCamera();$('scanner-result').textContent='Camera unavailable. Allow camera access on HTTPS or localhost, or upload a QR photo instead.';}
  };
  $('qr-photo').onchange=async event=>{
    const file=event.target.files[0];if(!file)return;
    stopCamera();$('scanner-result').textContent='Reading the QR photo…';
    event.target.disabled=true;
    let bitmap=null;
    try {
      if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>10*1024*1024) throw new Error('Choose a PNG, JPEG or WebP photo up to 10 MB.');
      bitmap=await createImageBitmap(file);
      const code=decodeQR(bitmap,bitmap.width,bitmap.height);
      if(!code)throw new Error('No readable QR found. Use a clear photo with the whole QR visible.');
      $('scan-code').value=code;await openCode(code);
    } catch(error) {$('scanner-result').textContent=error.message;}
    finally {bitmap?.close();event.target.disabled=false;event.target.value='';}
  };
  window.addEventListener('pagehide',stopCamera);

})();
