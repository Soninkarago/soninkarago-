import unittest,time
from unittest.mock import patch
import test_journey_experience as fixtures
import journey_notifications as n
class NativeNotifications(unittest.TestCase):
 def setUp(self):
  fixtures.JourneyExperience.setUp(self)
  for schema in n.SCHEMAS:self.c.execute(schema)
  self.c.execute('ALTER TABLE drivers ADD COLUMN status TEXT');self.c.execute('ALTER TABLE drivers ADD COLUMN online BOOLEAN');self.c.execute('ALTER TABLE drivers ADD COLUMN last_location_at BIGINT');self.c.execute("UPDATE drivers SET status='approved',online=TRUE,last_location_at=?",(int(time.time()),))
  self.c.execute("ALTER TABLE rides ADD COLUMN payment_status TEXT DEFAULT 'unpaid'");self.c.execute('ALTER TABLE rides ADD COLUMN offered_driver_id TEXT');self.c.execute('ALTER TABLE rides ADD COLUMN offer_expires_at BIGINT')
  # Worker needs fetchall on the same adapter.
  original=self.db
  from contextlib import contextmanager
  @contextmanager
  def db():
   with original() as conn:
    execute=conn.execute
    def run(q,args=()):
     c=execute(q,args);c.fetchall=lambda:[dict(r) for r in c.c.fetchall()];return c
    conn.execute=run;yield conn
  self.db=db;self.token='ExpoPushToken['+'A'*22+']'
 def bind(self,**extra):n.handle_post(self.app,'/api/journey/notifications',{'ride_id':'R','tracking_token':'private-token','push_token':self.token,'enabled':True,**extra},self.db);return self.response
 def test_binding_scoped_to_passenger_and_opt_out(self):
  self.assertEqual(self.bind(tracking_token='bad')[0],401);self.assertEqual(self.bind(push_token='https://attacker')[0],400)
  self.assertEqual(self.bind()[0],200);self.assertEqual(self.bind(enabled=False)[0],200)
  self.assertTrue(self.c.execute('SELECT revoked FROM journey_push_subscriptions').fetchone()[0])
 def test_state_change_sends_once_and_has_no_private_payload(self):
  self.bind();self.c.execute("UPDATE rides SET status='in_progress'")
  with patch.object(n,'expo_call',return_value={'data':{'status':'ok','id':'ticket'}}) as send:
   n.tick(self.db);n.tick(self.db);self.assertEqual(send.call_count,1)
   payload=send.call_args.args[1];self.assertNotIn('private-token',str(payload));self.assertNotIn('14.7',str(payload));self.assertEqual(payload['ttl'],60)
 def test_receipt_removes_unregistered_device(self):
  self.bind();self.c.execute("UPDATE rides SET status='in_progress'")
  with patch.object(n,'expo_call',return_value={'data':{'status':'ok','id':'ticket'}}):n.tick(self.db)
  self.c.execute('UPDATE journey_push_deliveries SET created_at=?',(int(time.time())-901,))
  with patch.object(n,'expo_call',return_value={'data':{'ticket':{'status':'error','details':{'error':'DeviceNotRegistered'}}}}):n.tick(self.db)
  self.assertTrue(self.c.execute('SELECT revoked FROM journey_push_subscriptions').fetchone()[0]);self.assertEqual(self.c.execute('SELECT status FROM journey_push_deliveries').fetchone()[0],'failed')
 def test_uncertain_gateway_response_does_not_duplicate(self):
  self.bind();self.c.execute("UPDATE rides SET status='in_progress'")
  with patch.object(n,'expo_call',side_effect=TimeoutError) as send:n.tick(self.db);n.tick(self.db);self.assertEqual(send.call_count,1)
  self.assertEqual(self.c.execute('SELECT status FROM journey_push_deliveries').fetchone()[0],'uncertain')

 def test_payment_change_while_searching_is_not_lost(self):
  self.c.execute("UPDATE rides SET status='searching'");self.bind();self.c.execute("UPDATE rides SET payment_status='fully_paid'")
  with patch.object(n,'expo_call',return_value={'data':{'status':'ok','id':'ticket'}}) as send:
   n.tick(self.db);self.assertEqual(send.call_count,1);self.assertIn('Paiement confirmé',send.call_args.args[1]['body'])
 def test_expired_or_revoked_binding_never_sends(self):
  self.bind();self.c.execute("UPDATE rides SET status='completed'");self.c.execute('UPDATE journey_push_subscriptions SET expires_at=0')
  with patch.object(n,'expo_call') as send:n.tick(self.db);send.assert_not_called()
 def test_gateway_handoff_does_not_claim_phone_received(self):
  self.bind();self.c.execute("UPDATE rides SET status='completed'")
  with patch.object(n,'expo_call',return_value={'data':{'status':'ok','id':'ticket'}}):n.tick(self.db)
  self.c.execute('UPDATE journey_push_deliveries SET created_at=?',(int(time.time())-901,))
  with patch.object(n,'expo_call',return_value={'data':{'ticket':{'status':'ok'}}}):n.tick(self.db)
  self.assertEqual(self.c.execute('SELECT status FROM journey_push_deliveries').fetchone()[0],'handed_off')
