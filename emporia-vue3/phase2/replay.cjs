'use strict';
const fs=require('node:fs'),{Appliance,DishwasherFeatures}=require('./logic.cjs');
const engines={range:new Appliance('range'),dryer:new Appliance('dryer'),dishwasher:new Appliance('dishwasher')};
const result={rows:0,transitions:[],healthGaps:{range:0,dryer:0,dishwasher:0},note:'Offline simulation from signed raw samples; not live automation output. Door/contact/temperature and notification dispatch are not replayed.'};
let health={};
for(const line of fs.readFileSync(process.argv[2],'utf8').trim().split('\n')){
 const r=JSON.parse(line);result.rows++;result.firstLocal??=r.local;result.lastLocal=r.local;
 for(const [key,s] of Object.entries(r.values)){
  for(const e of engines[key].evaluate(s,r.now)){
   if(e.kind==='transition')result.transitions.push({...e,local:r.local});
   if(e.kind==='health'&&health[key]!==false)result.healthGaps[key]++;
  }
  health[key]=s.available;
 }
}
console.log(JSON.stringify(result,null,2));
