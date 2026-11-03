import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';
const source=await fs.readFile('omniverse/web/smartport-speech.js','utf8');
const {encodeWav,spokenChunks}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const wav=new DataView(await encodeWav([new Float32Array([-2,-1,0,.5,1,2])],16000).arrayBuffer());
assert.equal(wav.getUint32(24,true),16000);assert.equal(wav.getUint16(22,true),1);assert.equal(wav.getUint32(40,true),12);
assert.equal(wav.getInt16(44,true),-32768);assert.equal(wav.getInt16(54,true),32767);
const spoken=spokenChunks('**智慧港口** [查看](https://example.test)\n```python\nsecret_tool_result\n```\n'+'设备正常。'.repeat(100));
assert.ok(spoken.every(part=>part.length<=220));assert.ok(!spoken.join('').includes('secret_tool_result'));assert.ok(!spoken.join('').includes('https:'));
assert.throws(()=>spokenChunks('x'.repeat(4001)));
let Recorder;const events=[];
vm.runInNewContext(await fs.readFile('omniverse/web/smartport-recorder.js','utf8'),{
 AudioWorkletProcessor:class{constructor(){this.port={postMessage:m=>events.push(m)};}},
 registerProcessor:(name,ctor)=>{Recorder=ctor;},Float32Array
});
const recorder=new Recorder();
for(let i=0;i<33;i++)recorder.process([[new Float32Array(128).fill(.25)]]);
recorder.port.onmessage({data:'stop'});
assert.equal(events.filter(e=>e.type==='chunk').reduce((n,e)=>n+e.samples.length,0),4224);
assert.equal(events.at(-1).type,'stopped');assert.equal(recorder.process([]),false);
console.log('PASS: PCM WAV encoding, bounded speech chunks, recording flush/stop');
