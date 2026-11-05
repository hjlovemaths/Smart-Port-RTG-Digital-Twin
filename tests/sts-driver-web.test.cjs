const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
async function main(){
  const events={},requests=[],intervals=[],nodes={};let hold=false,pending,readyEvents=0,clock=1000;
  const node=()=>({textContent:'',hidden:false,style:{},append(){},setAttribute(){},focus(){},querySelector(s){return nodes[s]??=node();}});
  const video=Object.assign(node(),{readyState:2,paused:false,addEventListener(name,fn){events['video:'+name]=fn;}});
  const document={documentElement:{classList:{contains:n=>n==='smartport-sts-driver'||n==='smartport-driver'}},head:node(),body:node(),createElement:node,
    getElementById:id=>id==='remote-video'?video:null,addEventListener(name,fn){events['doc:'+name]=fn;}};
  const window={addEventListener(name,fn){events[name]=fn;},dispatchEvent(e){if(e.type==='smartport-driver-ready')readyEvents++;}};
  const scope={document,window,performance:{now:()=>clock},crypto:{getRandomValues(a){a.fill(8);return a;}},Event:class{constructor(type){this.type=type;}},AbortController,console,
    setInterval(fn){intervals.push(fn);},setTimeout(fn,delay){if(delay===250)setImmediate(fn);return 1;},clearTimeout(){},
    navigator:{sendBeacon(url,body){requests.push({url,action:'release',body:JSON.parse(body)});}},location:{replace(){throw Error('unexpected redirect');}},
    fetch:async(url,options)=>{
      assert(url.startsWith('/api/sts-driver/'),'STS page must never send truck/admin commands');
      const action=url.split('/').pop().split('?')[0],body=options.body?JSON.parse(options.body):{};requests.push({url,action,body,method:options.method});
      const response={ok:true,status:200,json:async()=>({status:'driving',view:body.view||'under',fov:body.fov??140,stream_ready:action!=='acquire',input_seq:-1})};
      if(action==='input'&&hold){hold=false;return await new Promise(resolve=>pending=()=>resolve(response));}return response;
    }};
  vm.runInNewContext(fs.readFileSync('omniverse/web/smartport-sts-driver.js','utf8'),scope);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));for(let i=0;i<5;i++)await settle();
  const key=(type,code,repeat=false)=>{let consumed=false;events[type]({code,key:code,repeat,target:{closest:()=>null},preventDefault(){consumed=true;},stopImmediatePropagation(){}});assert(consumed);};
  assert.equal(requests[0].action,'acquire');assert.equal(readyEvents,1);
  for(const [code,axis,value] of [['KeyW','gantry',1],['KeyS','gantry',-1],['KeyD','trolley',1],['KeyA','trolley',-1],['KeyQ','hoist',1],['KeyE','hoist',-1]]){
    key('keydown',code);await settle();assert.equal(requests.at(-1).body[axis],value);
    key('keyup',code);await settle();assert.equal(requests.at(-1).body[axis],0);
  }
  for(let fov=130;fov>=10;fov-=10){key('keydown','KeyF');await settle();assert.equal(requests.at(-1).body.fov,fov);}
  key('keydown','KeyF',true);await settle();assert.equal(requests.at(-1).body.fov,10,'held F must not skip angles');
  key('keydown','KeyF');await settle();assert.equal(requests.at(-1).body.fov,140);
  hold=true;intervals[0]();await settle();key('keydown','KeyV');pending();await settle();await intervals[0]();
  assert.equal(requests.at(-1).body.view,'side','late replies must preserve requested view');
  key('keydown','KeyV');await settle();assert.equal(requests.at(-1).body.view,'end');
  key('keydown','KeyV');await settle();assert.equal(requests.at(-1).body.view,'under');
  for(const [code,field,value] of [['Digit1','size_ft',20],['Digit2','size_ft',40],['KeyK','lock',true],['KeyU','lock',false]]){
    key('keydown',code);await settle();assert.equal(requests.at(-1).body[field],value);
    await intervals[0]();assert.equal(requests.at(-1).body[field],undefined,'tool actions must not repeat on the input heartbeat');
    key('keydown',code,true);await settle();assert.equal(requests.at(-1).body[field],undefined);
  }
  hold=true;intervals[0]();await settle();key('keydown','Digit2');pending();await settle();await intervals[0]();
  assert.equal(requests.at(-1).body.size_ft,40,'a pending tool click must survive an in-flight heartbeat');
  key('keydown','Space');await settle();assert.equal(requests.at(-1).body.brake,true);
  events.blur();await settle();assert.equal(requests.at(-1).body.brake,true);
  await intervals[0]();assert.equal(requests.at(-1).body.brake,true,'an unfocused page must not resume auxiliary movement');
  video.readyState=0;key('keydown','KeyW');await settle();assert.equal(requests.at(-1).body.brake,true);
  const inputs=requests.filter(r=>r.action==='input');assert(inputs.every((r,i)=>i===0||r.body.seq>inputs[i-1].body.seq));
  events.pagehide();assert.equal(requests.at(-1).action,'release');assert.equal(requests.at(-1).body.client_id,requests[0].body.client_id);
  console.log('STS web: axes, size actions, FOV/views, focus/video loss and scoped release passed.');
  // Shared video-only class must not cause the truck module to acquire a truck.
  const count=requests.length;vm.runInNewContext(fs.readFileSync('omniverse/web/smartport-driver.js','utf8'),scope);assert.equal(requests.length,count);
}
main().catch(e=>{console.error(e);process.exitCode=1;});
