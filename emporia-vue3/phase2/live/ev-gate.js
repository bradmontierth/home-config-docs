// Serialized charger writes, changed-target dedup, bounded failures, readback.
const now=Date.now();
if(!globalThis._ev)globalThis._ev={desired:null,pending:null,lastKey:null,lastAttempt:0,lastRead:0,retryAt:0,seq:0,sent:0,acks:0,errors:0,readback:null,yielded:false};
const r=globalThis._ev,out=[[],[],[]];
if(msg.req){msg.payload={now,sent:r.sent,acks:r.acks,errors:r.errors,pending:!!r.pending,yielded:r.yielded,desired:r.desired?.payload||null,mode:r.desired?.debug?.result?.mode||null,reason:r.desired?.debug?.result?.reason||null,lastRead:r.lastRead,readback:r.readback};return [null,null,msg];}
if(msg.vueYield||msg.vueHold){r.desired=null;r.yielded=!!msg.vueYield;}
else if(msg.vueEvAck){
 if(r.pending&&r.pending.seq===msg.vueEvSeq){
  const sent=r.pending;r.pending=null;
  if(!msg.error&&msg.statusCode>=200&&msg.statusCode<300){r.lastKey=sent.key;r.acks++;r.retryAt=0;
   const amps=sent.payload.state==='active'?sent.payload.charge_current:0;
   const prev=flow.get('vueEvApplied');flow.set('vueEvApplied',{amps,since:amps?(prev?.amps>0?prev.since:now):null,at:now,mode:sent.mode});
  }else{r.errors++;r.retryAt=now+10000;}
 }
}else if(msg.vueEvRead){
 if(!msg.error&&msg.statusCode>=200&&msg.statusCode<300){r.readback=msg.payload;
  if(r.desired){const d=r.desired.payload;const match=msg.payload.state===d.state&&(d.state!=='active'||Number(msg.payload.charge_current)===Number(d.charge_current));if(!match)r.lastKey=null;}
 }
}else if(msg.vueInputs&&msg.payload&&['active','disabled'].includes(msg.payload.state)){
 r.yielded=false;r.desired={...msg};
}
if(r.pending&&now-r.pending.at>4000){r.pending=null;r.errors++;r.retryAt=now+10000;}
const d=r.desired;
if(d&&now-d.vueAt<15000&&!r.yielded){
 const urgent=d.payload.state==='disabled'&&(d.debug?.gridOutage===true||d.vueSafetyStop===true||d.debug?.touBlocked===true);
 const key=JSON.stringify(d.payload);
 if(!r.pending&&key!==r.lastKey&&(urgent&&key!==r.lastAttemptKey||now>=r.retryAt)&&(urgent||now-r.lastAttempt>=10000)){
  const seq=++r.seq;r.pending={seq,key,at:now,payload:d.payload,mode:d.debug?.result?.mode};r.sent++;r.lastAttempt=now;r.lastAttemptKey=key;
  out[0].push({...d,vueEvSeq:seq,requestTimeout:3000});
 }
}
if(now-r.lastRead>=30000){r.lastRead=now;out[1].push({url:'http://192.168.30.121/override',method:'GET',requestTimeout:3000,vueEvRead:true});}
node.status({fill:r.errors&&r.pending?'yellow':'green',shape:'dot',text:r.yielded?'manual control':r.sent+' writes / '+r.acks+' ack'});
return out.map(x=>x.length?x:null);
