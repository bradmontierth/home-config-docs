import json,pathlib,urllib.request,copy
w=pathlib.Path(open('/tmp/vue-energy-workdir').read());tab_id='afea81d223c193eb';t=json.load(urllib.request.urlopen('http://127.0.0.1:1880/flow/'+tab_id));p=w/(tab_id+'.before.json');p.write_text(json.dumps(t,indent=2));p.chmod(0o600)
channels=[]
for m in json.load(open('emporia-vue3/labeled-streams.json')):
 ch=int(m['input']) if m['input'].isdigit() else {'A':17,'B':18}[m['input']]
 channels.append({'channel':ch,'label':m['label'],'entity':'sensor.emporia_vue3_'+m['power_average'].lower().replace(' ','_'),'power':True})
for ch,label,eid in [(19,'L1 Voltage','l1_voltage'),(20,'Frequency','line_frequency'),(21,'L2 Voltage','l2_voltage')]:channels.append({'channel':ch,'label':label,'entity':'sensor.emporia_vue3_'+eid,'power':False})
pathlib.Path('emporia-vue3/energy/channels.json').write_text(json.dumps(channels,indent=2)+'\n')
def add(id,type,name,wires,**kw):
 n=dict(id=id,z=tab_id,type=type,name=name,x=400,y=650+len(t['nodes'])*20,wires=wires,**kw);t['nodes'].append(n);return n
def fn(id,name,func,wires):return add(id,'function',name,wires,func=func,outputs=len(wires),timeout=0,noerr=0,initialize='',finalize='',libs=[])
def inj(id,name,repeat,to):return add(id,'inject',name,[[to]],props=[{'p':'payload'},{'p':'topic','vt':'str'}],repeat=str(repeat),crontab='',once=True,onceDelay=12,topic='vue-minute',payload='',payloadType='date')
entities=[c['entity'] for c in channels]+['sensor.emporia_vue3_uptime'];template="{% set ns=namespace(rows=[]) %}{% for id in "+json.dumps(entities)+" %}{% set s=states[id] %}{% if s is not none %}{% set ns.rows=ns.rows+[{'entity_id':id,'state':s.state,'last_reported':s.last_reported.isoformat()}] %}{% endif %}{% endfor %}{{ ns.rows|to_json }}"
inj('vue3energyclock','Collect new minute reports (10s check)',10,'vue3energyapi')
add('vue3energyapi','ha-api','Vue minute average snapshot',[['vue3energyinsert']],server='23fd91e9137b71c5',version=1,debugenabled=False,protocol='http',method='post',path='/template',data=json.dumps({'template':template}),dataType='jsonata',responseType='json',outputProperties=[{'property':'payload','propertyType':'msg','value':'','valueType':'results'}])
fn('vue3energyinsert','Idempotent Vue minute ingestion','const CHANNELS='+json.dumps(channels)+';\n'+pathlib.Path('emporia-vue3/energy/minute-collector.js').read_text(),[['vue3energydb']])
add('vue3energydb','mysql','Vue minute SQL',[['vue3energyack']],mydb='50d88d05b3f142e9')
fn('vue3energyack','Minute SQL result','msg.vueMinuteAck=true;return msg;',[['vue3energyinsert']])
add('vue3energycatch','catch','Minute SQL failure',[['vue3energyack']],scope=['vue3energydb'],uncaught=False)
archive="""INSERT INTO VuePowerHourly (BucketStartUTC,Channel,AverageValue,WattHours,ObservedSeconds)
SELECT CAST(DATE_FORMAT(BucketStartUTC,'%Y-%m-%d %H:00:00') AS DATETIME), Channel,AVG(Value),SUM(WattHours),SUM(ObservedSeconds)
FROM VuePowerMinute
WHERE BucketStartUTC >= COALESCE((SELECT MAX(h.BucketStartUTC)-INTERVAL 2 HOUR FROM VuePowerHourly h),'2026-01-01')
AND BucketStartUTC < CAST(DATE_FORMAT(UTC_TIMESTAMP(),'%Y-%m-%d %H:00:00') AS DATETIME)
GROUP BY 1,2
ON DUPLICATE KEY UPDATE AverageValue=VALUES(AverageValue),WattHours=VALUES(WattHours),ObservedSeconds=VALUES(ObservedSeconds);"""
inj('vue3energyarchiveclock','Vue permanent hourly archive',3600,'vue3energyarchivequery');fn('vue3energyarchivequery','Archive completed Vue hours','msg.topic='+json.dumps(archive)+';delete msg.payload;return msg;',[['vue3energyarchivedb']]);add('vue3energyarchivedb','mysql','Vue hourly SQL',[[]],mydb='50d88d05b3f142e9')
p=w/(tab_id+'.candidate.json');p.write_text(json.dumps(t,indent=2));p.chmod(0o600);print('Prepared minute collector + hourly archive, existing 15s total feed unchanged')
