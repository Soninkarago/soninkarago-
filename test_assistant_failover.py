import json,os,unittest
from unittest.mock import patch
from urllib.error import HTTPError
import assistant_failover as f
class FailoverTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'GROQ_API_KEY':'dummy','ASSISTANT_FREE_ACCOUNT_CONFIRMED':'yes','ASSISTANT_ZDR_CONFIRMED':'yes','GEMINI_API_KEY':'dummy','ASSISTANT_GOOGLE_FREE_CONFIRMED':'yes','ASSISTANT_GOOGLE_GENERIC_ONLY_CONFIRMED':'yes'},clear=True);self.env.start();f._cooldown.clear()
 def tearDown(self):self.env.stop();f._cooldown.clear()
 def test_first_success_does_not_call_backups(self):
  with patch.object(f,'google_select') as google:
   calls=[]
   def groq(q,model,timeout):calls.append(model);return 'fare'
   self.assertEqual(f.select('prix',groq,{'fare','unknown'},True)[0],'fare');self.assertEqual(calls,[f.GROQ_MODELS[0]]);google.assert_not_called()
 def test_second_takes_over_first_failure(self):
  calls=[]
  def groq(q,model,timeout):
   calls.append(model)
   if len(calls)==1:raise TimeoutError()
   return 'fare'
  self.assertEqual(f.select('prix',groq,{'fare','unknown'})[1],'groq:'+f.GROQ_MODELS[1]);self.assertEqual(len(calls),2)
 def test_third_takes_over_both_failures(self):
  with patch.object(f,'google_select',return_value='fare') as google:
   def groq(*args,**kw):raise HTTPError('',429,'Quota',None,None)
   self.assertTrue(f.select('prix',groq,{'fare','unknown'},True)[1].startswith('google:'));google.assert_called_once()
 def test_three_failures_stop_and_cool_down(self):
  with patch.object(f,'google_select',side_effect=TimeoutError) as google:
   calls=[]
   def groq(*args,**kw):calls.append(kw['model']);raise TimeoutError()
   self.assertRaises(RuntimeError,f.select,'prix',groq,{'fare'},True);self.assertEqual(len(calls),2);self.assertEqual(google.call_count,1)
   self.assertRaises(RuntimeError,f.select,'prix',groq,{'fare'},True);self.assertEqual(len(calls),2);self.assertEqual(google.call_count,1)
 def test_google_never_without_separate_consent(self):
  with patch.object(f,'google_select') as google:
   def groq(*args,**kw):raise TimeoutError()
   self.assertRaises(RuntimeError,f.select,'prix',groq,{'fare'},False);google.assert_not_called()
 def test_invalid_first_answer_uses_second(self):
  calls=[]
  def groq(*args,**kw):calls.append(1);return 'fake' if len(calls)==1 else 'fare'
  self.assertEqual(f.select('prix',groq,{'fare'})[0],'fare');self.assertEqual(len(calls),2)
 def test_google_payload_never_contains_original_question(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return json.dumps({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'fare'}]}}]}).encode()
  with patch.object(f,'build_opener') as factory:
   factory.return_value.open.return_value=Response();self.assertEqual(f.google_select('Mamadou Diop 771234567 a payé ma course',{'fare','unknown'},4),'fare')
   body=json.loads(factory.return_value.open.call_args[0][0].data)
   self.assertNotIn('Mamadou',str(body));self.assertNotIn('771234567',str(body));self.assertNotIn('tools',body)
 def test_no_google_call_without_generic_keywords(self):
  with patch.object(f,'build_opener') as opener:self.assertEqual(f.google_select('Mamadou Diop',{'unknown'},4),'unknown');opener.assert_not_called()
 def test_provider_disabled_without_free_account_confirmation(self):
  del os.environ['ASSISTANT_FREE_ACCOUNT_CONFIRMED'];del os.environ['ASSISTANT_GOOGLE_FREE_CONFIRMED']
  with patch.object(f,'google_select') as google:
   groq=__import__('unittest.mock',fromlist=['Mock']).Mock();self.assertRaises(RuntimeError,f.select,'prix',groq,{'fare'},True);groq.assert_not_called();google.assert_not_called()
if __name__=='__main__':unittest.main()
