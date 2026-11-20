// Generated action table routes output messages to existing action nodes.
const now=Date.now();
if(!globalThis._dispatch)globalThis._dispatch={targets:{},sent:0,acked:0,failed:0};
const r=globalThis._dispatch,outs=Array.from({length:ACTIONS.length},()=>[]);
if(msg.vueAck){const x=r.targets[msg.vueGateKey];if(x&&x.inflight===msg.vueGateSeq){x.inflight=null;if(!msg.error&&(!msg.statusCode||(msg.statusCode>=200&&msg.statusCode<300))){x.acked=x.signature;r.acked++;}else{x.retry=now+10000;r.failed++;}}}
else if(msg.vueRoute){
 const index=ACTIONS.findIndex(a=>a.id===msg.vueRoute);if(index<0)return null;
 const a=ACTIONS[index],key=a.key;
 const signature=JSON.stringify([a.id,msg.state||null]);
 const old=r.targets[key];
 if(!old||old.signature!==signature)r.targets[key]={signature,index,msg:{...msg},acked:old?.acked,inflight:null,retry:0,attempts:0};
}
for(const [key,x] of Object.entries(r.targets)){
 if(x.acked===x.signature||now<x.retry)continue;
 if(x.inflight&&now-x.sentAt<15000)continue;
 if(x.attempts>=3){node.status({fill:'red',shape:'ring',text:'action failed; awaiting next intent'});continue;}
 // Power-sourced intents expire rather than firing later from an obsolete queue.
 if(x.msg.vueAt&&now-x.msg.vueAt>45000)continue;
 x.sentAt=now;x.retry=now+10000;x.inflight=(r.sent++)+1;x.attempts++;
 outs[x.index].push({...x.msg,vueGateKey:key,vueGateSeq:x.inflight});
}
node.status({fill:'green',shape:'dot',text:r.sent+' sent / '+r.acked+' ack / '+r.failed+' failed'});
return outs.map(x=>x.length?x:null);
