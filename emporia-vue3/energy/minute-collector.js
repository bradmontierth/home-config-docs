const now=Date.now();
if(!globalThis._minute)globalThis._minute={seen:context.get('vueMinuteSeen')||{},pending:null,seq:0,rows:0,errors:0};
const r=globalThis._minute;
if(msg.vueMinuteAck){
 if(r.pending?.seq===msg.vueMinuteSeq){
  if(msg.error){r.errors++;}else{for(const x of r.pending.rows)r.seen[x.channel]=x.bucket;r.rows+=r.pending.rows.length;context.set('vueMinuteSeen',r.seen);}
  r.pending=null;
 }
 node.status({fill:r.errors?'yellow':'green',shape:'dot',text:r.rows+' minute rows / '+r.errors+' errors'});return null;
}
if(r.pending&&now-r.pending.at<20000)return null;
let source=msg.payload;if(typeof source==='string'){try{source=JSON.parse(source);}catch{return null;}}
if(!Array.isArray(source))return null;
const rows=[],data=Object.fromEntries(source.map(x=>[x.entity_id,x]));
const uptime=data['sensor.emporia_vue3_uptime'];if(!uptime||Number(uptime.state)<120)return null;
for(const c of CHANNELS){
 const x=data[c.entity];if(!x||x.state===''||x.state===null)continue;
 const ts=Date.parse(x.last_reported),v=Number(x.state);
 if(!Number.isFinite(v)||v<0||!Number.isFinite(ts)||now-ts>90000||ts>now+2000)continue;
 // Each firmware power average represents the preceding 60-second window.
 const bucket=Math.floor(ts/60000)*60000-(c.power?60000:0);
 if(bucket<r.seen[c.channel]||bucket===r.seen[c.channel])continue;
 rows.push({channel:c.channel,bucket,reported:ts,value:v,wh:c.power?v/60:null});
}
if(!rows.length)return null;
const stamp=t=>new Date(t).toISOString().replace('T',' ').replace('Z','');
msg.topic='INSERT IGNORE INTO VuePowerMinute (BucketStartUTC,Channel,ReportedAtUTC,Value,WattHours,ObservedSeconds) VALUES '+rows.map(x=>`('${stamp(x.bucket)}',${x.channel},'${stamp(x.reported)}',${x.value},${x.wh===null?'NULL':x.wh},${x.wh===null?0:60})`).join(',')+';';
msg.vueMinuteSeq=++r.seq;r.pending={seq:r.seq,at:now,rows};delete msg.payload;return msg;
