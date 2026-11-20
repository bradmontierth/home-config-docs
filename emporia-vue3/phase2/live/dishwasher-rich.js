// One writer for dishwasher_new. Wall-clock windows; no sample-count assumptions.
const now=Date.now(),s=msg.vueSample;
if(!globalThis._rich){globalThis._rich={window:new Window(240000),tail:new Window(90000),state:'dirty',last:null,lastHi:null,activeSince:null,pending:null,cleanSince:null};}
const r=globalThis._rich;
if(msg.vueCurrentNew==='dirty'&&r.lastExternal!=='dirty'){r.state='dirty';r.pending=null;r.activeSince=null;}
r.lastExternal=msg.vueCurrentNew;
if(!valid(s,now)){r.window=new Window(240000);r.tail=new Window(90000);r.activeSince=null;r.pending=null;r.cleanSince=null;r.last=null;node.status({fill:'yellow',shape:'ring',text:'power unavailable'});return null;}
if(r.last!==null&&s.reportedAt-r.last>=20000){r.window=new Window(240000);r.tail=new Window(90000);r.pending=null;r.activeSince=null;r.cleanSince=null;r.lastHi=null;}
if(r.last===null||s.reportedAt>r.last){r.window.add(s.reportedAt,s.watts);r.tail.add(s.reportedAt,s.watts);r.last=s.reportedAt;if(s.watts>=650)r.lastHi=s.reportedAt;}
if(s.watts>100){if(r.activeSince===null)r.activeSince=now;}else r.activeSince=null;
const win=r.window.stats(now),tail=r.tail.stats(now),fast=r.activeSince!==null&&now-r.activeSince>=30000;
let proposed=r.state;
if(win.coverage>=0.95){
 const clean=win.mean<=25,running=!clean&&((win.max>=650&&win.amp<=120)||win.mean>=600);
 const recent=r.lastHi!==null&&now-r.lastHi<=7200000;
 const drying=!clean&&!running&&recent&&now-r.lastHi>=600000&&win.max<=600&&win.amp>=180&&win.mean>65;
 proposed=clean?'clean':drying?'drying':'running';
 if(tail.coverage>=0.95&&tail.mean<=30&&tail.max<=40)proposed='clean';
 if(r.state==='drying'){
  if(win.max>=650){proposed='running';r.cleanSince=null;}
  else if(clean){if(r.cleanSince===null)r.cleanSince=now;proposed=now-r.cleanSince>=120000?'clean':'drying';}
  else{r.cleanSince=null;proposed='drying';}
 }
}
if(['dirty','clean'].includes(r.state)&&fast)proposed='running';
// No power-only clean claim when the external dirty state has not run.
if(r.state==='dirty'&&proposed==='clean')proposed='dirty';
if(proposed!==r.state){
 if(!r.pending||r.pending.state!==proposed)r.pending={state:proposed,at:now};
 const immediate=(['dirty','clean'].includes(r.state)&&proposed==='running'&&fast)||(r.state==='drying'&&proposed==='clean');
 const duration=['dirty','clean'].includes(r.state)&&proposed==='running'?30000:240000;
 if(immediate||now-r.pending.at>=duration){r.state=proposed;r.pending=null;r.cleanSince=null;msg.state=r.state;msg.payload={};node.status({fill:'green',shape:'dot',text:r.state});return msg;}
}else r.pending=null;
node.status({fill:'green',shape:'ring',text:r.state+(win.coverage<.95?' (warming)':'')});return null;
