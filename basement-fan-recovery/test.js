const fs=require('fs'),vm=require('vm'),assert=require('assert');
const code=fs.readFileSync(__dirname+'/controller.js','utf8');
function harness(device='pixel8') {
 let now=1000000, data={}, errors=[];
 const run=msg=>vm.runInNewContext('(function(){'+code+'})()', {msg,Date:{now:()=>now},flow:{get:k=>data[k],set:(k,v)=>data[k]=v},global:{get:()=>device},node:{status:()=>{},error:x=>errors.push(x)}});
 const sample=(fan='unavailable',light='unavailable',power='on')=>run({kind:'snapshot',requestedAt:now,payload:['fan.basement_fan','light.basement_light','light.master_bath_fan_zooz_relay'].map((entity_id,i)=>({entity_id,state:[fan,light,power][i]}))});
 const advance=(ms)=>{now+=ms};
 const poll=(count,...states)=>{let r;for(let i=0;i<count;i++){advance(30000);r=sample(...states);}return r};
 return {run,sample,advance,poll,get s(){return data.recovery},errors};
}
let h=harness(); h.sample(); assert(!h.poll(3)); let r=h.poll(1);assert(r[1]);assert.equal(h.s.attempts,1);
assert(!h.sample());h.run({kind:'off_ok'});h.advance(29000);assert(!h.run({kind:'tick'}));h.advance(1000);assert(h.run({kind:'tick'})[2]);h.run({kind:'on_ok'});
assert(!h.poll(5));assert(h.poll(1)[1]);assert.equal(h.s.attempts,2);
h.run({kind:'off_ok'});h.advance(30000);assert(h.run({kind:'tick'})[2]);h.run({kind:'on_ok'});h.poll(5);r=h.poll(1);assert(r[3]);assert.equal(r[3].device,'pixel8');assert.equal(h.s.phase,'locked');assert(!h.poll(100));assert.equal(h.s.attempts,2);
h.sample('off','on');h.poll(19,'off','on');assert.equal(h.s.phase,'locked');h.poll(1,'off','on');assert.equal(h.s.attempts,0);
h=harness();h.sample('unavailable','on','off');h.poll(20,'unavailable','on','off');assert.equal(h.s.attempts,0);
h=harness();h.sample('off','off');h.poll(30,'off','off');assert.equal(h.s.attempts,0);
h=harness();h.sample('on','unknown');assert(h.poll(4,'on','unknown')[1]);h.advance(60000);assert(h.run({kind:'tick'})[2]); // lost OFF ack / restart still restores
h.advance(60000);assert(h.run({kind:'tick'})[2]);h.advance(60000);assert(h.run({kind:'tick'})[2]);h.advance(60000);assert(h.run({kind:'tick'})[3]);assert.equal(h.s.phase,'locked');
h=harness('');h.sample();h.poll(4);h.advance(60000);h.run({kind:'tick'});h.advance(60000);h.run({kind:'tick'});h.advance(60000);h.run({kind:'tick'});h.advance(60000);assert(!h.run({kind:'tick'}));assert.equal(h.errors.length,1);
h=harness();h.sample();h.advance(120000);assert(!h.sample());assert.equal(h.s.attempts,0); // HA outage does not count as continuous failure
console.log('PASS: debounce, either entity, normal off, intentional supply off, 2-attempt cap, single alert, stable rearm, restart/lost acknowledgement restoration, bounded ON retries, no broadcast, stale sampling gap');
