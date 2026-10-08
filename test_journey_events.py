import unittest,time,sqlite3,tempfile,os
from unittest.mock import patch
import test_journey_notifications as fixtures
import journey_events as events
import journey_notifications as notifications

class TransactionalEvents(unittest.TestCase):
 def setUp(self):
  fixtures.NativeNotifications.setUp(self)
  # SQLite fixture uses the same event columns and change predicate. PostgreSQL
  # PL/pgSQL installation is separately verified by deployment health/startup.
  values="lower(hex(randomblob(16))),NEW.id,NEW.status,NEW.payment_status,NEW.offered_driver_id,NEW.offer_expires_at,CAST(strftime('%s','now') AS INTEGER)*1000000+(SELECT COUNT(*) FROM journey_events)"
  columns='id,ride_id,status,payment_status,offered_driver_id,offer_expires_at,created_us'
  self.c.executescript(f'''CREATE TRIGGER events_update AFTER UPDATE ON rides WHEN NEW.status IS NOT OLD.status OR NEW.payment_status IS NOT OLD.payment_status OR NEW.offered_driver_id IS NOT OLD.offered_driver_id OR NEW.offer_expires_at IS NOT OLD.offer_expires_at BEGIN INSERT INTO journey_events({columns}) VALUES({values}); END;
  CREATE TRIGGER events_insert AFTER INSERT ON rides BEGIN INSERT INTO journey_events({columns}) VALUES({values}); END;''')
 def bind(self,**extra):return fixtures.NativeNotifications.bind(self,**extra)
 def rows(self):return self.c.execute('SELECT * FROM journey_events ORDER BY created_us,id').fetchall()
 def test_fast_transitions_between_polls_are_recorded(self):
  self.bind()
  for status in ['arriving','in_progress','completed']:self.c.execute('UPDATE rides SET status=?',(status,))
  self.assertEqual([r['status'] for r in self.rows()],['arriving','in_progress','completed'])
  with patch.object(notifications,'expo_call',return_value={'data':{'status':'ok','id':'ticket'}}) as send:
   notifications.tick(self.db);notifications.tick(self.db);self.assertEqual(send.call_count,1);self.assertIn('terminée',send.call_args.args[1]['body'])
 def test_same_status_write_and_gps_update_do_not_duplicate(self):
  self.c.execute("UPDATE rides SET status='arriving'");self.c.execute("UPDATE rides SET status='arriving',driver_lat=15")
  self.assertEqual(len(self.rows()),1)
 def test_payment_and_offer_are_recorded_independently(self):
  self.c.execute("UPDATE rides SET payment_status='failed'");self.c.execute("UPDATE rides SET offered_driver_id='D',offer_expires_at=?",(int(time.time())+15,))
  self.assertEqual(len(self.rows()),2);self.assertEqual(self.rows()[1]['offered_driver_id'],'D')
 def test_rolled_back_course_transition_rolls_back_event(self):
  self.c.commit();self.c.execute('BEGIN');self.c.execute("UPDATE rides SET status='completed'");self.assertEqual(len(self.rows()),1);self.c.rollback()
  self.assertEqual(len(self.rows()),0);self.assertEqual(self.c.execute('SELECT status FROM rides').fetchone()[0],'accepted')
 def test_old_journal_never_triggers_obsolete_push(self):
  self.bind();self.c.execute("UPDATE rides SET status='arriving'");self.c.execute('UPDATE journey_events SET created_us=?',((int(time.time())-61)*1000000,))
  with patch.object(notifications,'expo_call') as send:notifications.tick(self.db);send.assert_not_called()
 def test_event_endpoint_is_private_and_has_no_location_or_driver_data(self):
  self.c.execute("UPDATE rides SET status='completed'")
  self.app.path='/api/rides/R/events?token=bad';events.handle_get(self.app,'/api/rides/R/events',self.db);self.assertEqual(self.response[0],403)
  self.app.path='/api/rides/R/events?token=private-token';events.handle_get(self.app,'/api/rides/R/events',self.db)
  self.assertEqual(self.response[0],200);self.assertEqual(len(self.response[1]),1);self.assertEqual(set(self.response[1][0]),{'id','status','payment_status','created_us'})
  self.app.path='/api/rides/R/events';self.user={'role':'driver','driver_id':'OTHER'};events.handle_get(self.app,'/api/rides/R/events',self.db);self.assertEqual(self.response[0],403)
 def test_journal_survives_sqlite_restart(self):
  self.c.execute("UPDATE rides SET status='completed'");self.c.commit()
  with tempfile.TemporaryDirectory() as tmp:
   path=os.path.join(tmp,'events.sqlite');disk=sqlite3.connect(path);self.c.backup(disk);disk.close();reopened=sqlite3.connect(path)
   try:self.assertEqual(reopened.execute('SELECT status FROM journey_events').fetchone()[0],'completed')
   finally:reopened.close()
 def test_retention_keeps_active_course_events(self):
  self.c.execute("UPDATE rides SET status='arriving'");self.c.execute('UPDATE journey_events SET created_us=1')
  with self.db() as conn:events.prune(conn)
  self.assertEqual(len(self.rows()),1);self.c.execute("UPDATE rides SET status='completed'")
  with self.db() as conn:events.prune(conn)
  self.assertEqual(len(self.rows()),1);self.assertEqual(self.rows()[0]['status'],'completed')
 def test_new_registration_does_not_replay_prior_event(self):
  self.c.execute("UPDATE rides SET status='arriving'");self.bind()
  with patch.object(notifications,'expo_call') as send:notifications.tick(self.db);send.assert_not_called()
