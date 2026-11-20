// Poll-driven recovery. Persistent context retains attempts and pending power restoration.
const now = Date.now();
const enabled = true;
let s = flow.get('recovery') || {phase:'idle', attempts:0};
const save = () => { flow.set('recovery', s); node.status({fill:s.phase==='locked'?'red':'blue',shape:'dot',text:s.phase+' / '+s.attempts+' attempts'}); };
const out = (i, m={}) => { save(); const a=[null,null,null,null]; a[i]=m; return a; };
function alert(reason) {
    s.phase='locked'; s.reason=reason;
    const device=global.get('BradPhoneNM');
    if (typeof device!=='string' || !device.trim() || device.includes(',')) { save(); node.error('Missing single BradPhoneNM target; refusing Pushover broadcast'); return null; }
    if (s.alerted) { save(); return null; }
    s.alerted=true;
    return out(3,{device,title:'Basement guest room fan needs attention',priority:0,payload:reason+' Automatic power cycling is stopped until both fan and light are healthy for 10 minutes.'});
}
if (msg.kind==='off_ok' && s.phase==='off_pending') {
    s.phase='off'; s.due=now+30000; save(); return null;
}
if (msg.kind==='on_ok' && s.phase==='on_pending') {
    s.phase='waiting'; s.due=now+180000; save(); return null;
}
if (msg.kind==='tick') {
    if (['off','off_pending','on_pending'].includes(s.phase)) {
        if (now<s.due) return null;
        if ((s.onTries||0)>=3) return alert('Could not confirm restoring Zooz power after 3 turn-on commands. Check the wall switch.');
        s.phase='on_pending'; s.onTries=(s.onTries||0)+1; s.due=now+60000;
        return out(2,{});
    }
    return out(0,{kind:'snapshot',requestedAt:now});
}
if (msg.kind!=='snapshot' || !Array.isArray(msg.payload) || now-msg.requestedAt>15000) return null;
// A late read must never interrupt an in-progress power cycle.
if (['off','off_pending','on_pending'].includes(s.phase)) return null;
const ids=['fan.basement_fan','light.basement_light','light.master_bath_fan_zooz_relay'];
const e=Object.fromEntries(msg.payload.filter(x=>ids.includes(x.entity_id)).map(x=>[x.entity_id,x]));
flow.set('lastSnapshot',{at:now,states:Object.fromEntries(ids.map(id=>[id,e[id]?.state||'missing']))});
if (s.lastSample && now-s.lastSample>90000) { s.badSince=0; s.healthySince=0; }
s.lastSample=now;
if (ids.some(id=>!e[id])) { s.badSince=0; s.healthySince=0; save(); return null; }
const healthy=ids.slice(0,2).every(id=>['on','off'].includes(e[id].state));
const offline=ids.slice(0,2).some(id=>['unavailable','unknown'].includes(e[id].state));
const power=e[ids[2]].state;
if (healthy) {
    s.badSince=0;
    if (!s.healthySince) s.healthySince=now;
    if (now-s.healthySince>=600000) s={phase:'idle',attempts:0,lastSample:now,healthySince:s.healthySince};
    save(); return null;
}
s.healthySince=0;
if (s.phase==='locked') { save(); return null; }
if (s.phase==='waiting' && now>=s.due && power==='off') return alert('Zooz supply still reports OFF after power restoration. Check the wall switch.');
if (!offline || power!=='on') { s.badSince=0; save(); return null; }
if (s.phase==='waiting' && now<s.due) { save(); return null; }
if (s.attempts>=2) return alert('Fan/light are still offline after 2 power-cycle attempts with 3 minutes to reconnect after each.');
if (!s.badSince && s.phase!=='waiting') { s.badSince=now; save(); return null; }
if ((s.phase!=='waiting' && now-s.badSince<120000) || !enabled) { save(); return null; }
s.attempts++; s.phase='off_pending'; s.due=now+60000; s.onTries=0;
return out(1,{});
