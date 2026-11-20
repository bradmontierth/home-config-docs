exec(open('emporia-vue3/energy/grafana-migrate.py').read().split("ds=api(")[0])
changes=[]
for uid in ['aebnyjaoxeha8b','cebnyj6gvkzk0a']:
 current=api('/api/dashboards/uid/'+uid);d=copy.deepcopy(current['dashboard']);count=0
 for panel in d.get('panels',[]):
  for t in panel.get('targets',[]):
   if t.get('datasource',{}).get('uid')!='vue-energy-utc':continue
   s=t.get('rawSql','');old=s;local="CONVERT_TZ(DateDTS,'UTC','America/Denver')"
   s=re.sub(r'UNIX_TIMESTAMP\(\s*DATE\(\s*DateDTS\s*\)\s*\)',"UNIX_TIMESTAMP(CONVERT_TZ(DATE("+local+"),'America/Denver','UTC'))",s,flags=re.I)
   s=re.sub(r'\bDATE\(\s*DateDTS\s*\)', 'DATE('+local+')',s,flags=re.I)
   s=re.sub(r'UNIX_TIMESTAMP\(d\)',"UNIX_TIMESTAMP(CONVERT_TZ(d,'America/Denver','UTC'))",s,flags=re.I)
   if 'YEAR(DateDTS)' in s:
    s=s.replace('YEAR(DateDTS)','YEAR('+local+')').replace('MONTH(DateDTS)','MONTH('+local+')')
    s=s.replace("UNIX_TIMESTAMP(STR_TO_DATE(","UNIX_TIMESTAMP(CONVERT_TZ(STR_TO_DATE(").replace("'%Y-%m-%d')) AS", "'%Y-%m-%d'),'America/Denver','UTC')) AS")
   if s!=old:t['rawSql']=s;count+=1
 if count:
  (w/('grafana-'+uid+'.calendar-before.json')).write_text(json.dumps(current,indent=2));candidate={'dashboard':d,'folderUid':current['meta'].get('folderUid',''),'overwrite':True,'message':'Preserve Denver calendar days/months with UTC energy timestamps'};(w/('grafana-'+uid+'.candidate.json')).write_text(json.dumps(candidate,indent=2));changes.append(uid)
print('Calendar query candidates:',changes)
pathlib.Path('emporia-vue3/energy/calendar-dashboards.json').write_text(json.dumps(changes))
