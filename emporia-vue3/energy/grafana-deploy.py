exec(open('emporia-vue3/energy/grafana-migrate.py').read().split("ds=api(")[0])
verified=json.load(open('emporia-vue3/energy/grafana-verification.json'));assert len(verified)==92 and all(not r.get('error') for r in verified)
results=[]
for p in sorted(w.glob('grafana-*.candidate.json')):
 c=json.load(open(p));d=c['dashboard'];before=json.load(open(w/('grafana-'+d['uid']+'.before.json')))
 current=api('/api/dashboards/uid/'+d['uid']);assert current['dashboard']==before['dashboard'],'Concurrent dashboard change '+d['uid']
 reply=api('/api/dashboards/db',c);assert reply['status']=='success',reply
 after=api('/api/dashboards/uid/'+d['uid']);assert after['dashboard']['panels']==d['panels'];assert after['dashboard']['title']==d['title'];out=w/('grafana-'+d['uid']+'.deployed.json');out.write_text(json.dumps(after,indent=2));out.chmod(0o600)
 results.append(reply);print('Deployed and verified',d['title'],reply['url'],flush=True)
pathlib.Path('emporia-vue3/energy/grafana-deployment.json').write_text(json.dumps(results,indent=2)+'\n')
