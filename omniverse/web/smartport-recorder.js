// Captures mono PCM only. Output remains silent to avoid microphone feedback.
class SmartPortRecorder extends AudioWorkletProcessor {
  constructor(){super();this.buffer=new Float32Array(4096);this.used=0;this.active=true;
    this.port.onmessage=e=>{if(e.data==='stop'){this.flush();this.active=false;this.port.postMessage({type:'stopped'});}};}
  flush(){if(this.used){const samples=this.buffer.slice(0,this.used);this.port.postMessage({type:'chunk',samples},[samples.buffer]);this.used=0;}}
  process(inputs){
    if(!this.active)return false;
    const channels=inputs[0];
    if(channels?.length){for(let i=0;i<channels[0].length;i++){
      let value=0;for(const channel of channels)value+=channel[i];
      this.buffer[this.used++]=value/channels.length;
      if(this.used===this.buffer.length)this.flush();
    }}
    return true;
  }
}
registerProcessor('smartport-recorder',SmartPortRecorder);
