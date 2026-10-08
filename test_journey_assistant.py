import os,unittest,json
from unittest.mock import patch
import journey_assistant as a
class App:
 def __init__(self):self.result=None
 def sendj(self,data,status=200):self.result=(status,data)
 def check_rate(self,*args):return True
class AssistantTests(unittest.TestCase):
 def setUp(self):self.app=App();self.env=patch.dict(os.environ,{},clear=True);self.env.start()
 def tearDown(self):self.env.stop()
 def ask(self,body):a.handle_post(self.app,'/api/assistant',body);return self.app.result
 def test_disabled_without_key(self):
  a.handle_get(self.app,'/api/assistant');self.assertFalse(self.app.result[1]['available'])
 def test_no_implicit_activation(self):
  os.environ['GROQ_API_KEY']='test';self.assertFalse(a.configured())
 def test_manual_help_without_ai(self):
  with patch.object(a,'classify',side_effect=AssertionError('No provider call')):
   status,r=self.ask({'topic':'identity'});self.assertEqual(status,200);self.assertEqual(r['mode'],'guide');self.assertIn('plaque',r['answer'])
 def test_disabled_no_provider_request(self):
  with patch.object(a,'classify',side_effect=AssertionError('No provider call')):self.assertEqual(self.ask({'question':'Mon devis','consent':True})[0],503)
 def test_consent_required(self):self.assertEqual(self.ask({'question':'Mon devis'})[0],400)
 def test_limits(self):self.assertEqual(self.ask({'question':'x'*501,'consent':True})[0],400)
 def test_scrub(self):
  clean=a.clean_question('Mon email test@example.sn et téléphone +221 77 123 45 67');self.assertNotIn('test@example',clean);self.assertNotIn('221',clean)
 def test_provider_fail_no_retry_or_paid_fallback(self):
  with patch.object(a,'configured',return_value=True),patch.object(a.assistant_failover,'select',side_effect=TimeoutError) as mock:
   self.assertEqual(self.ask({'question':'Un devis','consent':True})[0],503);self.assertEqual(mock.call_count,1)
 def test_ai_can_only_select_preapproved_text(self):
  with patch.object(a,'configured',return_value=True),patch.object(a.assistant_failover,'select',return_value=('fare','groq:test')):
   _,r=self.ask({'question':'Combien coûte ma course ?','consent':True});self.assertEqual(r['answer'],a.ANSWERS['fare'][1]);self.assertFalse(r['request_created'])
 def test_emergency_not_dispatched(self):
  _,r=self.ask({'topic':'emergency'});self.assertIn('1515',r['answer']);self.assertFalse(r['request_created'])
 def test_law_is_not_a_legal_decision(self):
  _,r=self.ask({'topic':'legal'});self.assertTrue(r['requires_human']);self.assertIn('ne tranche pas',r['answer'])
 def test_real_adapter_rejects_invented_text(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'Un remboursement de 5000 FCFA est garanti'}}]}).encode()
  with patch.dict(os.environ,{'GROQ_API_KEY':'dummy'}),patch('urllib.request.build_opener') as factory:
   factory.return_value.open.return_value=Response();self.assertRaises(ValueError,a.classify,'remboursement')
   req=factory.return_value.open.call_args[0][0];body=json.loads(req.data);self.assertNotIn('tools',body);self.assertEqual(body['model'],a.MODEL)
if __name__=='__main__':unittest.main()
