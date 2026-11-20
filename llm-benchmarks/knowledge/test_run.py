"""Offline checks for streaming measurements; never calls a paid API."""
import importlib.util, json, unittest
from pathlib import Path
import httpx
spec=importlib.util.spec_from_file_location('knowledge_runner',Path(__file__).with_name('run.py'))
runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)

def event(value): return 'data: '+json.dumps(value)+'\n\n'

class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_split_separator_and_final_usage(self):
        content=''.join(event({'id':'test','choices':[{'delta':{'content':s}}]}) for s in ['Direct answer.\n===MO','RE===\n','Details.'])
        content+=event({'choices':[],'usage':{'cost':0.012,'total_tokens':50}})+'data: [DONE]\n\n'
        transport=httpx.MockTransport(lambda r:httpx.Response(200,text=content,headers={'content-type':'text/event-stream'}))
        async with httpx.AsyncClient(transport=transport) as client:
            row=await runner.complete(client,{}, {},'https://benchmark.invalid','===MORE===')
        self.assertEqual(row['spoken'],'Direct answer.')
        self.assertIsNotNone(row['spoken_ready_ms'])
        self.assertEqual(row['usage']['cost'],0.012)
        self.assertIsNone(row['error'])
    async def test_error_after_partial_answer_is_not_success(self):
        content=event({'choices':[{'delta':{'content':'Partial'}}]})+event({'error':{'message':'provider failed'}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=content))) as client:
            row=await runner.complete(client,{}, {},'https://benchmark.invalid','===MORE===')
        self.assertIn('provider failed',row['error'])
        self.assertIsNone(row['spoken_ready_ms'])
        self.assertIsNone(row['usage'])

if __name__=='__main__': unittest.main()
