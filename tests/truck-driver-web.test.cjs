const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');

async function main(){
  const events={},requests=[],intervals=[],nodes={};let pending=null,hold=false,statePolls=0,readyEvents=0,abortRequest=null;
  const node=()=>({textContent:'',hidden:false,style:{},append(){},setAttribute(){},focus(){},
    querySelector(selector){return nodes[selector]??=(node());}});
  const video=Object.assign(node(),{readyState:2,paused:false,addEventListener(name,fn){events['video:'+name]=fn;}});
  const document={documentElement:{classList:{contains:name=>name==='smartport-driver'}},head:node(),body:node(),
    createElement:node,getElementById:id=>id==='remote-video'?video:null,addEventListener(name,fn){events['doc:'+name]=fn;}};
  const window={addEventListener(name,fn){events[name]=fn;},dispatchEvent(event){if(event.type==='smartport-driver-ready')readyEvents++;}};
  const scope={document,window,performance:{now:()=>1000},crypto:{getRandomValues(a){a.fill(7);return a;}},
    Event:class{constructor(type){this.type=type;}},AbortController,console,
    setInterval(fn){intervals.push(fn);},setTimeout(fn,delay){if(delay===250)setImmediate(fn);if(delay===8000)abortRequest=fn;return 1;},clearTimeout(){},
    navigator:{sendBeacon(url,body){requests.push({action:'release',body:JSON.parse(body)});}},location:{replace(){throw new Error('unexpected login redirect');}},
    fetch:async(url,options)=>{
      const action=url.split('/').pop().split('?')[0],body=options.body?JSON.parse(options.body):{client_id:new URL(url,'http://test').searchParams.get('client_id')};requests.push({action,body,method:options.method});
      if(action==='state')statePolls++;
      // Cold-start state requests can time out while Kit initializes RTX.
      // Retain the acquire lease and retry rather than abandoning startup.
      if(action==='state'&&statePolls===1){abortRequest();throw options.signal.reason;}
      const response={ok:true,status:200,json:async()=>({status:'driving',view:body.view||'cabin',speed_kmh:0,input_seq:-1,
        stream_ready:action==='acquire'?false:action==='state'?statePolls>=2:true})};
      if(action==='input'&&hold){hold=false;return await new Promise(resolve=>pending=()=>resolve(response));}
      return response;
    }};
  vm.runInNewContext(fs.readFileSync('omniverse/web/smartport-driver.js','utf8'),scope);
  const settle=()=>new Promise(resolve=>setImmediate(resolve));for(let i=0;i<6;i++)await settle();
  const key=(type,code,key=code)=>{
    let consumed=false;
    events[type]({code,key,repeat:false,target:{closest:()=>null},preventDefault(){consumed=true;},stopImmediatePropagation(){}});
    return consumed;
  };
  assert.equal(requests[0].action,'acquire');assert.equal(requests[0].body.client_id.length,32);
  assert.equal(statePolls,2);assert.equal(readyEvents,1,'connect only after the private stream is ready');
  assert(requests.filter(r=>r.action==='state').every(r=>r.method==='GET'&&r.body.client_id===requests[0].body.client_id));
  assert(key('keydown','KeyW','w'));await settle();assert.equal(requests.at(-1).body.throttle,1);
  key('keyup','KeyW','w');await settle();assert.equal(requests.at(-1).body.throttle,0);
  hold=true;intervals[0]();await settle();
  key('keydown','KeyV','v');pending();await settle();await intervals[0]();
  assert.equal(requests.at(-1).body.view,'follow','late replies must not discard a requested camera switch');
  key('keydown','Space',' ');await settle();assert.equal(requests.at(-1).body.brake,true);
  events.blur();await settle();assert.equal(requests.at(-1).body.throttle,0);assert.equal(requests.at(-1).body.brake,true);
  video.readyState=0;key('keydown','KeyW','w');await settle();
  assert.equal(requests.at(-1).body.throttle,0);assert.equal(requests.at(-1).body.brake,true);
  const inputs=requests.filter(r=>r.action==='input');
  assert(inputs.every((r,i)=>i===0||r.body.seq>inputs[i-1].body.seq));
  events.pagehide();assert.equal(requests.at(-1).action,'release');
  assert.equal(requests.at(-1).body.client_id,requests[0].body.client_id);
  console.log('Driver web controls: forward, brake, camera switching, deadman, video loss, sequencing and release passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
