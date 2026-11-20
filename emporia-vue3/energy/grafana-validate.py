exec(open('emporia-vue3/energy/grafana-migrate.py').read().split("ds=api(")[0])
from concurrent.futures import ThreadPoolExecutor,as_completed
import time
jobs=[]
def walk(x):
 if isinstance(x,dict):
  for t in x.get('targets',[]):
   if t.get('datasource',{}).get('uid')=='vue-energy-utc':yield x.get('id'),x.get('title'),t,x.get('datasource')
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
for f in w.glob('grafana-*.candidate.json'):
 d=api('/api/dashboards/uid/'+json.load(open(f))['dashboard']['uid'])['dashboard']
 for pid,title,t,pds in walk(d):
  for label,fr,to in [('recent','now-2h','now'),('archive','1704067200000','1706745600000')]:jobs.append((d['uid'],pid,title,t,pds,label,fr,to))
def check(job):
 uid,pid,title,t,pds,label,fr,to=job;q=copy.deepcopy(t);effective=t['datasource'] if pds.get('uid')=='-- Mixed --' else pds;assert effective.get('uid')=='vue-energy-utc',(uid,pid,effective);q.update(refId='A',datasource=effective,intervalMs=60000,maxDataPoints=1000)
 start=time.time()
 try:
  r=api('/api/ds/query',{'queries':[q],'from':fr,'to':to});rs=r.get('results',{}).get('A',{});error=rs.get('error');frames=rs.get('frames',[]);counts=[len((f.get('data',{}).get('values') or [[]])[0]) for f in frames];return {'uid':uid,'panel':pid,'title':title,'range':label,'error':error,'rows':sum(counts),'seconds':round(time.time()-start,3)}
 except urllib.error.HTTPError as e:
  return {'uid':uid,'panel':pid,'title':title,'range':label,'error':e.read().decode()[:600]}
results=[]
with ThreadPoolExecutor(max_workers=3) as pool:
 for f in as_completed([pool.submit(check,j) for j in jobs]):
  r=f.result();results.append(r)
  if r.get('error'):print('ERROR',r,flush=True)
pathlib.Path('emporia-vue3/energy/grafana-verification.json').write_text(json.dumps(results,indent=2)+'\n');print('Queries checked',len(results),'errors',sum(bool(r.get('error')) for r in results),'nonempty',sum(r.get('rows',0)>0 for r in results),flush=True)
assert all(not r.get('error') for r in results)
