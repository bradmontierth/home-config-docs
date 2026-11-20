"""Correct panel routing while preserving the effective source of unrelated queries."""
import copy
UTC = {'type': 'mysql', 'uid': 'vue-energy-utc'}
OLD = {'type': 'mysql', 'uid': 'eebnvy9j8hmgwb'}
MIXED = {'type': 'datasource', 'uid': '-- Mixed --'}

def repair(dashboard):
 changes=[]
 def walk(p):
  targets=p.get('targets',[])
  regular=[t for t in targets if t.get('datasource',{}).get('uid')!='__expr__']
  if any(t.get('datasource',{}).get('uid')==UTC['uid'] for t in regular):
   previous=copy.deepcopy(p.get('datasource') or OLD)
   others=[t for t in regular if t.get('datasource',{}).get('uid')!=UTC['uid']]
   if others:
    # Non-mixed panels previously routed all ordinary targets via panel source.
    if previous.get('uid')!='-- Mixed --':
     for t in others:t['datasource']=copy.deepcopy(previous)
    desired=MIXED
   else:desired=UTC
   if p.get('datasource')!=desired:
    p['datasource']=copy.deepcopy(desired);changes.append(p.get('id'))
  if dashboard['uid']=='cebnyj1w2a680f' and p.get('id')==22:
   import re
   for t in targets:
    s=t.get('rawSql','')
    for col in ['ClaireMini','Office','Microwave','WasherBidetLaundry','Garage','MasterBedroomCloset','BasementBathroom','LoftKidsBathWHF']:
     s=re.sub(r'avg\('+col+r'\)(\s+AS\s+)', 'avg('+col+')/'+('240' if col=='ClaireMini' else '120')+r'\1', s, flags=re.I)
    t['rawSql']=s
   p['title']='Estimated amps (power / nominal voltage)'
   p['description']='Estimated current from real power divided by nominal 120 V or 240 V. This is not measured RMS current; power factor affects the estimate. Unmetered remainder uses 120 V as an approximation.'
  for child in p.get('panels',[]):walk(child)
 for panel in dashboard.get('panels',[]):walk(panel)
 return changes

if __name__=='__main__':
 exec(open('emporia-vue3/energy/grafana-migrate.py').read().split('ds=api(')[0])
 import datetime
 backup=w/('panel-routing-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));backup.mkdir()
 for uid in ['cebnyj1w2a680f','aebnyja8531moa','debnyj9w02t4wa','cebnyj6gvkzk0a','aebnyjaoxeha8b']:
  original=api('/api/dashboards/uid/'+uid);(backup/(uid+'.before.json')).write_text(json.dumps(original,indent=2))
  d=copy.deepcopy(original['dashboard']);changes=repair(d)
  if d==original['dashboard']:continue
  result=api('/api/dashboards/db',{'dashboard':d,'folderUid':original['meta'].get('folderUid',''),'overwrite':False,'message':'Fix energy panel datasource routing and estimated current units'})
  after=api('/api/dashboards/uid/'+uid);(backup/(uid+'.after.json')).write_text(json.dumps(after,indent=2))
  assert not repair(copy.deepcopy(after['dashboard']))
  print(uid,changes,result['status'])
 print('Backups:',backup)
