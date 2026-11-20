const fs=require('fs'),vm=require('vm');
function runner(path){const mem=new Map();let now=1700000000000;const sandbox={context:{get:k=>mem.get(k),set:(k,v)=>mem.set(k,v)},global:{get:k=>k==='BradPhoneNM'?'test':undefined},node:{status:()=>{},warn:()=>{}},Date:class extends Date{static now(){return now}},msg:{}};vm.createContext(sandbox);const script=new vm.Script('(function(){'+fs.readFileSync(path,'utf8')+'})()');return (msg,t)=>{now=1700000000000+t*1000;sandbox.msg=msg;return script.runInContext(sandbox)}}
for(const dt of [10,5,1]){
 const run=runner(__dirname+'/dishwasher-original.js');let first=null;
 for(let t=0;t<=100;t+=dt){let r=run({payload:800,ts:1700000000000+t*1000},t);if(r.state==='running'){first=t;break;}}
 console.log(JSON.stringify({detector:'dishwasher',intervalSeconds:dt,firstRunningSeconds:first}));
 const outage=runner(__dirname+'/dryer-outage-original.js');let alarm=null;
 for(let t=0;t<=100;t+=dt){let r=outage({payload:{col8:1000},gridOutage:'on'},t);if(r[2]){alarm=t;break;}}
 console.log(JSON.stringify({detector:'dryerOutage',intervalSeconds:dt,firstAlertSeconds:alarm}));
}
