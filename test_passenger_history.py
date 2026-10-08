import unittest,json
import passenger_history as h
import test_journey_experience as fixtures
class PassengerHistory(unittest.TestCase):
 def setUp(self):
  fixtures.JourneyExperience.setUp(self)
  for schema in h.SCHEMAS:self.c.execute(schema)
  for column in ('pickup TEXT','destination TEXT','fare INTEGER','payment TEXT','payment_status TEXT','created_at INTEGER'):self.c.execute('ALTER TABLE rides ADD COLUMN '+column)
  self.c.execute("UPDATE rides SET pickup='Village A',destination='Village B',fare=2500,payment='cash',payment_status='deposit_paid',created_at=1")
  self.nonce='synthetic-only-'+'N'*43;self.secret='test-only-auth-secret'
 def call(self,action,**data):
  h.handle_post(self.app,'/api/passenger/history/'+action,data,self.db,self.secret);return self.response
 def create(self,nonce=None):
  code,r=self.call('create',request_key=nonce or self.nonce,consent=True);self.assertEqual(code,200);return r['recovery_key']
 def test_explicit_consent_required_and_storage_contains_no_raw_key(self):
  self.assertEqual(self.call('create',request_key=self.nonce,consent=False)[0],400)
  self.assertEqual(self.call('create',request_key='short',consent=True)[0],400)
  key=self.create();self.assertEqual(len(key),64);row=dict(self.c.execute('SELECT * FROM passenger_history_accounts').fetchone());self.assertNotIn(key,str(row));self.assertNotIn(self.nonce,str(row))
 def test_lost_create_response_reuses_exact_account_and_key(self):
  key=self.create();self.assertEqual(key,self.create());self.assertEqual(self.c.execute('SELECT COUNT(*) FROM passenger_history_accounts').fetchone()[0],1)
 def test_foreign_tracking_key_rejected_and_double_attachment_idempotent(self):
  key=self.create();self.assertEqual(self.call('link',recovery_key=key,ride_id='R',tracking_token='wrong')[0],403)
  for _ in range(2):self.assertEqual(self.call('link',recovery_key=key,ride_id='R',tracking_token='private-token')[0],200)
  self.assertEqual(self.c.execute('SELECT COUNT(*) FROM passenger_history_links').fetchone()[0],1)
 def test_accounts_are_isolated_even_when_phone_unknown(self):
  a=self.create();b=self.create('B'*43);self.call('link',recovery_key=a,ride_id='R',tracking_token='private-token');self.c.execute("UPDATE rides SET status='completed'")
  self.assertEqual(self.call('list',recovery_key=b)[1]['rides'],[]);self.assertEqual(len(self.call('list',recovery_key=a)[1]['rides']),1)
 def test_active_ride_hidden_then_real_payment_and_stops_returned_without_private_fields(self):
  key=self.create();self.call('link',recovery_key=key,ride_id='R',tracking_token='private-token')
  self.assertEqual(self.call('list',recovery_key=key)[1]['rides'],[])
  self.c.execute("UPDATE rides SET status='completed'");self.c.execute('INSERT INTO journey_routes(ride_id,route_polyline,quote_token,stops) VALUES(?,?,?,?)',('R','test-polyline','test-quote',json.dumps([{'address':'Village C','lat':14.7,'lng':-17.4}])))
  r=self.call('list',recovery_key=key)[1]['rides'][0];self.assertEqual(r['stops'],[{'address':'Village C'}]);self.assertEqual(r['payment_status'],'deposit_paid')
  for private in ('tracking_token','driver_id','driver_lat','driver_lng','driver_name','polyline'):self.assertNotIn(private,r)
 def test_invalid_keys_cannot_read_or_delete(self):
  self.create()
  for action in ('list','delete','link'):
   for key in ('bad','f'*64,None):self.assertEqual(self.call(action,recovery_key=key)[0],401)
 def test_delete_retry_does_not_delete_course_or_recreate_old_vault(self):
  key=self.create();self.call('link',recovery_key=key,ride_id='R',tracking_token='private-token')
  for _ in range(2):self.assertEqual(self.call('delete',recovery_key=key)[0],200)
  self.assertEqual(self.c.execute('SELECT COUNT(*) FROM passenger_history_links').fetchone()[0],0);self.assertEqual(self.c.execute('SELECT COUNT(*) FROM rides').fetchone()[0],1)
  self.assertEqual(self.call('list',recovery_key=key)[0],410);self.assertEqual(self.call('create',request_key=self.nonce,consent=True)[0],410)
 def test_auth_secret_rotation_does_not_return_wrong_recovery_key(self):
  self.create();self.secret='rotated-test-secret';self.assertEqual(self.call('create',request_key=self.nonce,consent=True)[0],409)
