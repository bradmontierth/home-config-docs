exec(open('/tmp/vue-energy-ha-migrate.py').read().split('manifest=json')[0])
import copy
r=json.load(open('/tmp/vue-energy-ha-result.json'));circuits=r['circuits'];config=ws('lovelace/config',url_path='dash-with-sections');before=copy.deepcopy(config)
pathlib.Path('/tmp/vue-energy-lovelace.before.json').write_text(json.dumps(config,indent=2))
v=next(v for v in config['views'] if v.get('path')=='iotawatt');v['title']='Power & Energy'
def entities(title,rows):return {'type':'entities','title':title,'show_header_toggle':False,'entities':[{'entity':e,'name':n} for e,n in rows]}
v['cards']=[entities('Home, solar and batteries',[
 ('sensor.emporia_vue3_home_power_live','Total home load · includes outdoor heat pumps'),('sensor.emporia_vue3_mains_a_power_live','Panel main A'),('sensor.emporia_vue3_mains_b_power_live','Panel main B'),('sensor.iotawattsolar_total_heat_pump_power','Outdoor heat pumps'),('sensor.tesla_powerwall_3_pw3_total_solar','Solar production'),('sensor.tesla_powerwall_3_pw3_total_power_out','Powerwall flow'),('sensor.dashboard_energy_calculations_effective_home_usage','Home load + battery charging'),('sensor.powerwalls_average','Battery charge')]),
 entities('Live circuit power',[(c['live'],c['label']) for c in circuits]),
 entities('Energy today', [('sensor.daily_home_and_heat_pump_energy','Total home'),('sensor.solar_powerwall_energy_daily','Solar production'),('sensor.heat_pump_daily_energy','Outdoor heat pumps')]+[(c['daily'],c['label']) for c in circuits]),
 {'type':'history-graph','title':'Panel load · minute averages','hours_to_show':24,'entities':[{'entity':'sensor.emporia_vue3_mains_a_power_1m_average','name':'Main A'},{'entity':'sensor.emporia_vue3_mains_b_power_1m_average','name':'Main B'}]},
 entities('Supply', [('sensor.emporia_vue3_l1_voltage','L1 voltage'),('sensor.emporia_vue3_l2_voltage','L2 voltage'),('sensor.emporia_vue3_line_frequency','Frequency')]),
 {'type':'markdown','content':'Daily totals continue from the previous meters where the circuit matches. New circuit counters began September 27; today is a partial day. Historical Upstairs Beds and Utility Closet series are retained separately because their old coverage is not confirmed on the new CTs. Outdoor heat pumps are measured separately from the Vue panel mains.'}]
# Replace unambiguous retired live references elsewhere in the same dashboard.
old={'fridge':1,'dryer':2,'ac_total':3,'range':4,'furnace':7,'water_heater':8,'kitchen':10,'ev_charging':11,'claire_mini_split':12,'office':13};ports={c['port']:c for c in circuits};replacements={'sensor.iotawatt_'+k+'_power':ports[n]['live'] for k,n in old.items()};replacements.update({'sensor.iotawatt_voltage':'sensor.emporia_vue3_l1_voltage','sensor.iotawatt_frequency':'sensor.emporia_vue3_line_frequency'})
def replace(x):
 if isinstance(x,dict):return {k:replace(v) for k,v in x.items()}
 if isinstance(x,list):return [replace(v) for v in x]
 return replacements.get(x,x) if isinstance(x,str) else x
config=replace(config)
states={s['entity_id'] for s in api('states')}
for card in v['cards']:
 for e in card.get('entities',[]):assert e['entity'] in states,e['entity']
assert ws('lovelace/config',url_path='dash-with-sections')==before,'Concurrent dashboard edit'
ws('lovelace/config/save',url_path='dash-with-sections',config=config)
assert ws('lovelace/config',url_path='dash-with-sections')==config
pathlib.Path('/tmp/vue-energy-lovelace.after.json').write_text(json.dumps(config,indent=2));print('HA Power & Energy view saved and re-read; existing /iotawatt path preserved')
