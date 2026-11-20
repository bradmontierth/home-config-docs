import pathlib,json,re,sys
sys.path.insert(0,str(pathlib.Path(__file__).parent));from db import query
w=pathlib.Path(open('/tmp/vue-energy-workdir').read());sql=[]
views=['v_iotawatt_power_all','v_iotawatt_energy_all','v_iotawatt_solar_power_all','v_iotawatt_solar_energy_all','v_iotawatt_solar_accounting_all','v_solar_cost_trend_all','v_energy_power_energy_all','v_iotawatt_daily']
for v in views:
 p=w/(v+'.before.sql')
 if not p.exists():p.write_text(query("SELECT VIEW_DEFINITION FROM information_schema.VIEWS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='"+v+"';").split('\n',1)[1].strip());p.chmod(0o600)
for v in ['v_iotawatt_power_all','v_iotawatt_energy_all']:
 sql.append('CREATE OR REPLACE VIEW '+v+'_legacy AS '+(w/(v+'.before.sql')).read_text()+';')
cols={'WaterHeater':8,'Furnace':7,'Fridge':1,'Kitchen':10,'ACTotal':3,'DryerTotal':2,'EVTotal':11,'RangeTotal':4,'Office':13,'ClaireMini':12,'Microwave':5,'WasherBidetLaundry':6,'Garage':9,'MasterBedroomCloset':14,'BasementBathroom':15,'LoftKidsBathWHF':16}
energy={'Total':'TotalWattHours','ACTotal':'ACHours','DryerTotal':'DryerHours','RangeTotal':'RangeHours','WaterHeater':'WaterHeaterHours','EVTotal':'EVHours','Furnace':'FurnaceHours','Fridge':'FridgeHours','Kitchen':'KitchenHours','AllOther':'AllOtherHours','UpstairsBeds':'UpstairsBedsHours','Office':'OfficeHours','UtilityCloset':'UtilityClosetHours','ClaireMini':'ClaireMiniHours'}
for n in list(cols):energy.setdefault(n,n+'Hours')
powerorder=['Total','WaterHeater','Furnace','Fridge','Kitchen','ACTotal','DryerTotal','EVTotal','RangeTotal','AllOther','Voltage','Frequency','UpstairsBeds','Office','UtilityCloset','ClaireMini','Microwave','WasherBidetLaundry','Garage','MasterBedroomCloset','BasementBathroom','LoftKidsBathWHF','VoltageL2']
energyorder=['Total','ACTotal','DryerTotal','RangeTotal','WaterHeater','EVTotal','Furnace','Fridge','Kitchen','AllOther','UpstairsBeds','Office','UtilityCloset','ClaireMini','Microwave','WasherBidetLaundry','Garage','MasterBedroomCloset','BasementBathroom','LoftKidsBathWHF']
for kind,value,order in [('power','Value',powerorder),('energy','WattHours',energyorder)]:
 fields=[]
 for key in order:
  name=energy[key] if kind=='energy' else key
  if key=='Total':expr=f'CASE WHEN COUNT(CASE WHEN Channel IN (17,18) THEN 1 END)=2 THEN SUM(CASE WHEN Channel IN (17,18) THEN {value} ELSE 0 END) END'
  elif key=='AllOther':expr=f'CASE WHEN COUNT(CASE WHEN Channel BETWEEN 1 AND 18 THEN 1 END)=18 THEN SUM(CASE WHEN Channel IN (17,18) THEN {value} WHEN Channel BETWEEN 1 AND 16 THEN -{value} ELSE 0 END) END'
  elif key in ['UpstairsBeds','UtilityCloset']:expr='NULL'
  else:
   ch=cols.get(key,{'Voltage':19,'Frequency':20,'VoltageL2':21}.get(key));expr=f'MAX(CASE WHEN Channel={ch} THEN {value} END)'
  fields.append(expr+' AS `'+name+'`')
 ts='Time' if kind=='power' else 'DateDTS'
 sql.append(f'CREATE OR REPLACE VIEW v_vue_{kind}_minute AS SELECT BucketStartUTC AS `{ts}`, '+',\n'.join(fields)+' FROM VuePowerMinute '+('WHERE Channel<=18 ' if kind=='energy' else '')+'GROUP BY BucketStartUTC HAVING COUNT(CASE WHEN Channel<=18 THEN 1 END)>0;')
 existing=query("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='v_iotawatt_"+kind+"_all_legacy' ORDER BY ORDINAL_POSITION;").splitlines()[1:]
 allcols=[energy[k] if kind=='energy' else k for k in order]
 # Local-time compatibility for existing Node-RED / SQL consumers.
 legacyselect=', '.join('`'+c+'`' if c in existing else 'NULL AS `'+c+'`' for c in allcols)
 newselect=', '.join('`'+c+'`' for c in allcols)
 sql.append(f"CREATE OR REPLACE VIEW v_iotawatt_{kind}_all AS SELECT `{ts}`, {legacyselect} FROM v_iotawatt_{kind}_all_legacy WHERE `{ts}` < COALESCE((SELECT CONVERT_TZ(MIN(BucketStartUTC),'UTC','America/Denver') FROM VuePowerMinute),'9999-12-31') UNION ALL SELECT CONVERT_TZ(`{ts}`,'UTC','America/Denver'), {newselect} FROM v_vue_{kind}_minute;")
 sql.append(f"CREATE OR REPLACE VIEW v_home_{kind}_all AS SELECT CONVERT_TZ(`{ts}`,'America/Denver','UTC') AS `{ts}`, {legacyselect} FROM v_iotawatt_{kind}_all_legacy WHERE `{ts}` < COALESCE((SELECT CONVERT_TZ(MIN(BucketStartUTC),'UTC','America/Denver') FROM VuePowerMinute),'9999-12-31') UNION ALL SELECT `{ts}`, {newselect} FROM v_vue_{kind}_minute;")
# The retained outdoor source uses local timestamps; canonical Grafana views use UTC.
for old,new,ts in [('v_iotawatt_solar_power_all','v_home_solar_power_all','Time'),('v_iotawatt_solar_energy_all','v_home_solar_energy_all','DateDTS'),('v_iotawatt_solar_accounting_all','v_home_solar_accounting_all','DateDTS'),('v_solar_cost_trend_all','v_home_solar_cost_trend_all','DateDTS')]:
 cs=query("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='"+old+"' ORDER BY ORDINAL_POSITION;").splitlines()[1:]
 sql.append("CREATE OR REPLACE VIEW "+new+" AS SELECT CONVERT_TZ(`"+ts+"`,'America/Denver','UTC') AS `"+ts+"`, "+', '.join('`'+c+'`' for c in cs if c!=ts)+' FROM '+old+';')
pathlib.Path('emporia-vue3/energy/views.sql').write_text('\n\n'.join(sql)+'\n');print('Prepared',len(sql),'views, preserving legacy table data and local-time SQL contracts')
