// Retain sixty seconds; compare all power sources over the same trailing 5s window.
function alignedFrame(r,samples,now){
 const xs=Object.values(samples);
 if(!xs.every(x=>valid(x,now)))return {ok:false,reason:'missing/stale power source'};
 const end=Math.min(...xs.map(x=>x.reportedAt)),start=end-5000,result={};
 for(const [key,s] of Object.entries(samples)){
  const rows=r.history[key]||[];let area=0,coverage=0;
  for(let i=0;i<rows.length;i++){
   const x=rows[i],a=Math.max(start,x.t),b=Math.min(end,rows[i+1]?.t??end,x.t+20000);
   if(b>a){area+=(b-a)*x.w;coverage+=b-a;}
  }
  if(coverage<4999)return {ok:false,reason:'aligned window warming',source:key};
  result[key]={watts:area/coverage,reportedAt:end,available:true};
 }
 return {...evPowerTuple(result,now),samples:result,windowEnd:end,windowMs:5000};
}
