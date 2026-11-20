import json,pathlib,urllib.request,subprocess,copy,re,base64
w=pathlib.Path(open('/tmp/vue-energy-workdir').read());base='http://127.0.0.1:3001'
genv=dict(x.split('=',1) for x in json.loads(subprocess.check_output(['docker','inspect','grafana-grafana-1']))[0]['Config']['Env'] if '=' in x)
auth='Basic '+base64.b64encode((genv['GF_SECURITY_ADMIN_USER']+':'+genv['GF_SECURITY_ADMIN_PASSWORD']).encode()).decode()
def api(path,data=None,method=None):
 req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json','Authorization':auth});return json.load(urllib.request.urlopen(req,timeout=90))
ds=api('/api/datasources/uid/eebnvy9j8hmgwb');new={k:v for k,v in ds.items() if k not in ['id','uid','orgId','version','secureJsonFields','readOnly']};new.update(uid='vue-energy-utc',name='Home Energy (UTC)',isDefault=False);new['jsonData']['timezone']='+00:00'
# Reuse the existing database service account; do not print credentials.
env=dict(x.split('=',1) for x in json.loads(subprocess.check_output(['docker','inspect','mariadb-mariadb-1']))[0]['Config']['Env'] if '=' in x)
new['user']=env.get('MARIADB_USER',env.get('MYSQL_USER'));password=env.get('MARIADB_PASSWORD',env.get('MYSQL_PASSWORD'));assert new['user'] and password
new['secureJsonData']={'password':password}
try:existing=api('/api/datasources/uid/vue-energy-utc')
except urllib.error.HTTPError as e:
 if e.code!=404:raise
 existing=None
if existing:api('/api/datasources/uid/vue-energy-utc',new,'PUT')
else:api('/api/datasources',new)
print('UTC energy datasource configured')
mapviews={'v_iotawatt_power_all':'v_home_power_all','v_iotawatt_energy_all':'v_home_energy_all','v_iotawatt_solar_power_all':'v_home_solar_power_all','v_iotawatt_solar_energy_all':'v_home_solar_energy_all','v_iotawatt_solar_accounting_all':'v_home_solar_accounting_all','v_solar_cost_trend_all':'v_home_solar_cost_trend_all'}
extra_power=['ClaireMini','Office','Microwave','WasherBidetLaundry','Garage','MasterBedroomCloset','BasementBathroom','LoftKidsBathWHF']
labels={'Kitchen':'Dishwasher','ClaireMini':'Claire mini split','Office':'Office','Microwave':'Microwave','WasherBidetLaundry':'Washer / bidet / laundry','Garage':'Garage','MasterBedroomCloset':'Master bedroom / closet','BasementBathroom':'Basement bathroom','LoftKidsBathWHF':'Loft / kids bath / whole-house fan'}
changed=[]
for f in sorted(w.glob('grafana-*.before.json')):
 original=json.load(open(f));d=copy.deepcopy(original['dashboard']);count=0
 def walk(x):
  global count
  if isinstance(x,dict):
   for t in x.get('targets',[]):
    s=t.get('rawSql','')
    if not s or not any(k in s for k in [*mapviews,'v_energy_power_energy_all']):continue
    for old,newname in mapviews.items():s=re.sub(r'\b'+old+r'\b',newname,s)
    # Modern breakdowns keep old-only circuits as historical lines, adding the new measured circuits.
    if 'v_home_power_all' in s and all(k in s for k in ['WaterHeater','Fridge','AllOther']):
     aggregate=bool(re.search(r'\bavg\(',s,re.I));columns=[]
     for col in extra_power:
      if not re.search(r'\b'+col+r'\b',s):columns.append(('avg('+col+')' if aggregate else col)+' AS "'+labels[col]+'"')
     if columns:s=re.sub(r'\bFROM v_home_power_all',', '+', '.join(columns)+'\nFROM v_home_power_all',s,flags=re.I)
    if 'v_home_energy_all' in s and all(k in s for k in ['ACHours','FridgeHours','AllOtherHours']):
     cs=[]
     for col in extra_power:
      name=col+'Hours'
      if not re.search(r'\b'+name+r'\b',s):cs.append('SUM('+name+') AS "'+labels[col]+'"')
     if cs:s=re.sub(r'\bFROM v_home_energy_all',', '+', '.join(cs)+'\nFROM v_home_energy_all',s,flags=re.I)
    s=s.replace('"Kitchen"','"Dishwasher"').replace('"Upstairs Beds"','"Legacy Upstairs Beds"').replace('"Utility Closet"','"Legacy Utility Closet"').replace('"All Other"','"Unmetered / legacy Other"')
    t['rawSql']=s;t['datasource']={'type':'mysql','uid':'vue-energy-utc'};count+=1
   for val in x.values():
    if isinstance(val,(list,dict)):walk(val)
  elif isinstance(x,list):
   for val in x:walk(val)
 walk(d)
 if not count:continue
 if d['uid']=='aebnyja8531moa':d['title']='Home Energy'
 if d['uid']=='cebnyj1w2a680f':d['title']='Home Power'
 note='Vue 3 circuit data from September 27, 2026; earlier IotaWatt history retained. Circuit history uses 60-second averages. Upstairs Beds and Utility Closet are legacy-only; new CTs are named separately. Other/remainder coverage changed at migration. Panel total excludes separately metered outdoor heat pumps. Energy queries use UTC-normalized timestamps.'
 if d['uid'] in ['aebnyja8531moa','cebnyj1w2a680f']:d['description']=note;d['refresh']='30s'
 for p in d.get('panels',[]):
  if p.get('title') in ['Total Usage','Total Power']:p['title']='Panel '+p['title'].lower()+' (outdoor heat pumps separate)'
 if d['uid']=='debnyj9w02t4wa':
  d['description']='Vue household consumption + Powerwall solar after September 27, 2026; legacy Iota history retained. Energy-value estimates retain the existing flat rates and do not represent battery-aware utility billing. Solar/load overlap is estimated from minute energy.'
  for n in d['panels']:
   if n.get('id')==10:n['title']='Estimated net energy value'
   if n.get('id')==12:n['title']='Estimated household energy cost'
   if n.get('id')==13:n['title']='Direct solar / load overlap (estimate)'
 if d['uid']=='aebnyjaoxeha8b':
  for n in d['panels']:
   if n.get('id') in [10,13]:n['title']='Estimated net energy value'
 import runpy
 runpy.run_path('emporia-vue3/energy/grafana-panel-routing.py')['repair'](d)
 candidate={'dashboard':d,'folderUid':original['meta'].get('folderUid',''),'overwrite':True,'message':'Vue 3 circuit energy migration; preserve legacy history and normalize energy query timestamps'}
 (w/('grafana-'+d['uid']+'.candidate.json')).write_text(json.dumps(candidate,indent=2));changed.append({'uid':d['uid'],'title':d['title'],'targets':count})
pathlib.Path('emporia-vue3/energy/grafana-changes.json').write_text(json.dumps(changed,indent=2)+'\n');print('Prepared',changed)
