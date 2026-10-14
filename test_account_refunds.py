import hashlib,time,unittest
from unittest.mock import patch
import test_journey_experience as fixtures
import test_journey_http as http_fixtures
import passenger_account as accounts
import ride_refunds as refunds
import server

class PassengerAccounts(unittest.TestCase):
 def setUp(self):
  fixtures.JourneyExperience.setUp(self)
  for schema in accounts.SCHEMAS:self.c.execute(schema)
  self.sent=[]
  for name in ('pickup','destination','fare','payment','payment_status','created_at','passenger_payment_url'):self.c.execute('ALTER TABLE rides ADD COLUMN '+name+' TEXT')
 def post(self,action,data):
  accounts.handle_post(self.app,'/api/passenger/account/'+action,data,self.db,'isolated-secret',server.valid_recovery_email,lambda:True,lambda email,code:self.sent.append((email,code)),lambda *args:True)
  return self.response
 def login(self,email='one@example.org'):
  status,result=self.post('request',{'email':email,'consent':True});self.assertEqual(status,202)
  body={'challenge':result['challenge'],'code':self.sent[-1][1]}
  status,result=self.post('verify',body);self.assertEqual(status,200);return result['session'],body
 def test_recovery_session_retry_ownership_and_logout(self):
  token,body=self.login();self.assertEqual(self.post('verify',body)[1]['session'],token)
  self.assertFalse(self.post('me',{'session':token})[1]['phone_verified'])
  self.assertEqual(self.post('link',{'session':token,'ride_id':'R','tracking_token':'wrong'})[0],403)
  self.assertEqual(self.post('link',{'session':token,'ride_id':'R','tracking_token':'private-token'})[0],200)
  recovered,_=self.login();self.assertEqual(self.post('me',{'session':recovered})[1]['rides'][0]['id'],'R')
  foreign,_=self.login('two@example.org');self.assertEqual(self.post('me',{'session':foreign})[1]['rides'],[])
  self.post('logout',{'session':token});self.assertEqual(self.post('me',{'session':token})[0],401)
  self.assertIn(self.post('verify',body)[0],(400,409))
 def test_expired_code_attempt_limit_and_failed_email(self):
  _,r=self.post('request',{'email':'one@example.org','consent':True});body={'challenge':r['challenge'],'code':self.sent[-1][1]}
  for _ in range(5):self.assertEqual(self.post('verify',{**body,'code':'999999' if body['code']!='999999' else '000000'})[0],400)
  self.assertEqual(self.post('verify',body)[0],400)
  _,r=self.post('request',{'email':'one@example.org','consent':True});body={'challenge':r['challenge'],'code':self.sent[-1][1]};self.c.execute('UPDATE passenger_account_codes SET expires_at=0');self.assertEqual(self.post('verify',body)[0],400)

 def test_delivery_failure_and_deleted_session_cannot_recover(self):
  def fail(*args):raise TimeoutError()
  accounts.handle_post(self.app,'/api/passenger/account/request',{'email':'one@example.org','consent':True},self.db,'isolated-secret',server.valid_recovery_email,lambda:True,fail,lambda *args:True)
  self.assertEqual(self.response[0],503)
  self.assertNotIn('challenge',self.response[1])
  token,body=self.login();self.assertEqual(self.post('delete',{'session':token})[0],200)
  self.assertEqual(self.post('me',{'session':token})[0],401)
  self.assertEqual(self.post('verify',body)[0],409)
  self.assertEqual(self.c.execute('SELECT COUNT(*) FROM passenger_accounts').fetchone()['COUNT(*)'],1)

class CancellationHTTP(unittest.TestCase):
 def setUp(self):
  http_fixtures.JourneyHTTP.setUp(self)
  with self.db() as conn:
   conn.execute('ALTER TABLE rides ADD COLUMN cancelled_at BIGINT');conn.execute('ALTER TABLE rides ADD COLUMN cancel_reason TEXT')
 stop=http_fixtures.JourneyHTTP.stop
 call=http_fixtures.JourneyHTTP.call
 profile=http_fixtures.JourneyHTTP.profile
 def book(self):
  payload={'request_key':'C'*43,'route_code':'local_moto','quote_token':'signed-test','pickup_code_enabled':True,'journey_features':True,'client_name':'Client fictif','phone':'770000000','payment':'Espèces','terms_accepted':True,'privacy_accepted':True}
  status,ride=self.call('/api/rides',payload);self.assertEqual(status,201);return ride,'/api/rides/'+ride['id']
 def test_cancel_accepted_cash_restores_commission_once_and_blocks_after_pickup(self):
  self.profile();ride,path=self.book();token=ride['tracking_token']
  self.assertEqual(self.call(path+'/accept',{'pickup_code_supported':True})[0],200)
  with self.db() as conn:charged=conn.execute('SELECT balance FROM drivers WHERE id=%s',('D',)).fetchone()['balance']
  self.assertEqual(charged,9700)
  self.assertTrue(self.call(path+'/cancellation-preview?token='+token)[1]['automatic'])
  self.assertEqual(self.call(path+'/cancel',{'tracking_token':token})[0],200);self.assertEqual(self.call(path+'/cancel',{'tracking_token':token})[0],200)
  with self.db() as conn:self.assertEqual(conn.execute('SELECT balance FROM drivers WHERE id=%s',('D',)).fetchone()['balance'],10000)

 def test_pickup_started_cannot_cancel_or_return_commission(self):
  self.profile();ride,path=self.book();token=ride['tracking_token']
  self.assertEqual(self.call(path+'/accept',{'pickup_code_supported':True})[0],200)
  with self.db() as conn:conn.execute('UPDATE journey_codes SET verified_at=%s WHERE ride_id=%s',(int(time.time()),ride['id']))
  self.assertFalse(self.call(path+'/cancellation-preview?token='+token)[1]['automatic'])
  self.assertEqual(self.call(path+'/cancel',{'tracking_token':token})[0],409)
  with self.db() as conn:self.assertEqual(conn.execute('SELECT balance FROM drivers WHERE id=%s',('D',)).fetchone()['balance'],9700)
 def test_paid_cancel_refund_worker_never_repeats_and_callback_is_bound(self):
  ride,path=self.book();token=ride['tracking_token'];transaction='PRIVATE-PAYMENT-TOKEN'
  with self.db() as conn:
   r=conn.execute('SELECT * FROM rides WHERE id=%s',(ride['id'],)).fetchone();refunds.record_payment(conn,r,3000,transaction)
   conn.execute("UPDATE rides SET payment='Wave',payment_status='fully_paid' WHERE id=%s",(ride['id'],))
  self.assertEqual(self.call(path+'/cancel',{'tracking_token':token})[1]['refund']['status'],'queued')
  with patch.object(refunds,'request_refund',return_value=True) as send:refunds.tick(self.db,'key','secret');refunds.tick(self.db,'key','secret');send.assert_called_once()
  data={'ref_command':ride['id'],'token':transaction,'type_event':'refund_complete','item_price':3000,'currency':'XOF','env':'prod','api_key_sha256':refunds.digest('key'),'api_secret_sha256':refunds.digest('secret')}
  with patch.object(server,'PAYTECH_API_KEY','key'),patch.object(server,'PAYTECH_API_SECRET','secret'),patch.object(server,'PAYTECH_ENV','prod'):
   self.assertEqual(self.call('/api/paytech/refund-ipn',{**data,'token':'wrong'})[0],409)
   self.assertEqual(self.call('/api/paytech/refund-ipn',{**data,'item_price':2999})[0],409)
   self.assertEqual(self.call('/api/paytech/refund-ipn',{**data,'env':'test'})[0],403)
   self.assertEqual(self.call('/api/paytech/refund-ipn',data)[0],200);self.assertEqual(self.call('/api/paytech/refund-ipn',data)[0],200)
  self.assertEqual(self.call(path+'/report?token='+token)[1]['refunded_amount'],3000)
 def test_late_payment_does_not_revive_cancelled_course(self):
  ride,path=self.book();token=ride['tracking_token']
  with self.db() as conn:conn.execute("UPDATE rides SET payment='Wave',status='awaiting_payment' WHERE id=%s",(ride['id'],))
  self.assertEqual(self.call(path+'/cancel',{'tracking_token':token})[1]['refund']['status'],'awaiting_payment')
  with self.db() as conn:
   row=conn.execute('SELECT * FROM rides WHERE id=%s',(ride['id'],)).fetchone()
   refunds.record_payment(conn,row,3000,'late-private-transaction')
   self.assertEqual(conn.execute('SELECT status FROM rides WHERE id=%s',(ride['id'],)).fetchone()['status'],'cancelled')
   self.assertEqual(refunds.fields(conn,ride['id'])['refund']['status'],'queued')

 def test_uncertain_refund_is_not_resent(self):
  with self.db() as conn:conn.execute("INSERT INTO ride_refunds(ride_id,amount,status,created_at,updated_at) VALUES('R',3000,'queued',1,1)")
  with patch.object(refunds,'request_refund',side_effect=TimeoutError) as send:refunds.tick(self.db,'key','secret');refunds.tick(self.db,'key','secret');send.assert_called_once()
  with self.db() as conn:self.assertEqual(refunds.fields(conn,'R')['refund']['status'],'manual_review')
