// Independent driver picture when the installed NVIDIA SDK rejects a second WebRTC stream.
export function startJpegFrames(role, clientId) {
  const picture=document.createElement('img');
  picture.setAttribute('aria-label','司机独立画面');
  picture.style.cssText='position:fixed;inset:0;margin:auto;width:100vw;height:100vh;object-fit:contain;background:#070d13;z-index:10010';
  document.body.append(picture);
  let stopped=false,lastFrame=0,previousUrl=null;
  async function poll(){
    if(stopped)return;
    try{
      const response=await fetch(`/api/${role}/frame?client_id=${encodeURIComponent(clientId)}`,{cache:'no-store'});
      if(response.ok){
        const url=URL.createObjectURL(await response.blob());
        picture.onload=()=>{lastFrame=performance.now();if(previousUrl)URL.revokeObjectURL(previousUrl);previousUrl=url;};
        picture.src=url;
      }
    }catch(error){console.warn('Driver picture:',error);}
    if(!stopped)setTimeout(poll,100);
  }
  void poll();
  return {
    ready:()=>lastFrame>0&&performance.now()-lastFrame<1200,
    close:()=>{stopped=true;picture.remove();if(previousUrl)URL.revokeObjectURL(previousUrl);}
  };
}
