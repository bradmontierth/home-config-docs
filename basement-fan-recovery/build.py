import json,pathlib,copy
root=pathlib.Path(__file__).parent
ns=json.load(open('/tmp/nr-1880.json'))
def template(t): return copy.deepcopy(next(n for n in ns if n['type']==t))
nodes=[]
def add(n,id,name,x,y,wires):
 n.update(id='bgfr_'+id,z='new',name=name,x=x,y=y,wires=[['bgfr_'+a for a in w] for w in wires]); nodes.append(n); return n
add({'type':'inject','props':[{'p':'kind','v':'tick','vt':'str'}],'repeat':'30','crontab':'','once':True,'onceDelay':'45','topic':''},'tick','Check every 30 seconds',170,140,[['controller']])
add({'type':'function','func':(root/'controller.js').read_text(),'outputs':4,'timeout':0,'noerr':0,'initialize':'','finalize':'','libs':[]},'controller','Recovery: max 2 / persistent lockout',480,140,[['snapshot'],['off'],['on'],['push']])
n=add(template('ha-api'),'snapshot','Read live HA states',480,300,[['controller']]); n.update(protocol='http',method='get',path='states',data='',dataType='json',responseType='json',outputProperties=[{'property':'payload','propertyType':'msg','value':'','valueType':'results'}])
for action,y in [('off',80),('on',180)]:
 n=add(template('api-call-service'),action,'Zooz power '+action,860,y,[[action+'_ok']]); n.update(action='light.turn_'+action,domain='light',service='turn_'+action,entityId=['light.master_bath_fan_zooz_relay'],areaId=[],deviceId=[],floorId=[],labelId=[],data='{}',dataType='json',blockInputOverrides=True,queue='none',outputProperties=[])
 add({'type':'change','rules':[{'t':'set','p':'kind','pt':'msg','to':action+'_ok','tot':'str'}]},action+'_ok','Confirm '+action,1110,y,[['controller']])
add({'type':'pushover api','keys':'a3903de6e20b03dc','title':'','wires':[]},'push','Brad phone only (guarded)',850,280,[])
add({'type':'catch','scope':['bgfr_off','bgfr_on','bgfr_snapshot'],'uncaught':False},'catch','Log API / command errors',200,400,[['debug']])
add({'type':'debug','active':True,'tosidebar':True,'console':True,'tostatus':False,'complete':'error','targetType':'msg'},'debug','Recovery errors (bounded retries)',510,400,[])
flow={'label':'Basement Guest Fan Recovery','nodes':nodes,'configs':[],'info':'Modern Forms fan.basement_fan + light.basement_light. Supply: light.master_bath_fan_zooz_relay (Zooz). Poll live HA every 30s. Either unavailable/unknown for 2m while supply ON -> off 30s -> on -> wait 3m. Maximum 2 attempts, persistent lockout until both healthy 10m. Intentional supply OFF is respected. Restore power is prioritized across deploys/restarts; turn-on failures bounded to 3. Pushover only global.BradPhoneNM; no broadcast fallback. Source and tests: /home/pi/home_config/basement-fan-recovery.'}
(root/'flow.json').write_text(json.dumps(flow,indent=2)+'\n')
