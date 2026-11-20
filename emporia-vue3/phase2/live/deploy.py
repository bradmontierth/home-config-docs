import json,pathlib,urllib.request,datetime,hashlib,sys
work=pathlib.Path(open('/tmp/vue-phase2-workdir').read())
ids=sys.argv[1:] or ['0711e9167e62e249','ee0f1588760b9b3b','7fa25727b15db1f0','8123861fc119b96b','afea81d223c193eb']
def get(i):return json.load(urllib.request.urlopen('http://127.0.0.1:1880/flow/'+i))
def canon(x):
 def normalize(v):
  if isinstance(v,list):return [normalize(y) for y in v]
  if isinstance(v,dict):return {k:normalize(y) for k,y in v.items() if not(k=='wires' and not y)}
  return v
 return json.dumps(normalize(x),sort_keys=True,separators=(',',':'))
def baseline(i):
 p=work/(i+'.deployed.json')
 return p if p.exists() else work/(i+'.before.json')
for i in ids:
 assert canon(get(i))==canon(json.load(open(baseline(i)))), 'Live drift: '+i
for i in ids:
 before=json.load(open(baseline(i)));assert canon(get(i))==canon(before),'Live drift: '+i
 data=(work/(i+'.candidate.json')).read_bytes();req=urllib.request.Request('http://127.0.0.1:1880/flow/'+i,data=data,method='PUT',headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=60) as res:result=json.load(res)
 live=get(i);candidate=json.loads(data);assert live['id']==i
 assert canon(live['nodes'])==canon(candidate['nodes']),'Readback mismatch: '+i
 p=work/(i+'.deployed.json');p.write_text(json.dumps(live,indent=2));p.chmod(0o600)
 print('Deployed and verified',live['label'],len(live['nodes']),'nodes',flush=True)
(work/'deployed-at.txt').write_text(datetime.datetime.now(datetime.timezone.utc).isoformat())
