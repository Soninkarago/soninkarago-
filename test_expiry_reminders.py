import sqlite3
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch
import server


class Cursor:
    def __init__(self, cursor): self.cursor = cursor
    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None
    def fetchall(self): return [dict(row) for row in self.cursor.fetchall()]


class ExpiryReminders(unittest.TestCase):
    def setUp(self):
        self.today = date.today()
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript('''
          CREATE TABLE drivers(id TEXT PRIMARY KEY,status TEXT,recovery_email TEXT,recovery_email_verified BOOLEAN,
            driving_licence_expiry TEXT,insurance_expiry TEXT,technical_inspection_expiry TEXT);
          CREATE TABLE driver_expiry_notifications(id TEXT PRIMARY KEY,driver_id TEXT,kind TEXT,expiry TEXT,
            milestone INTEGER,status TEXT,attempts INTEGER,next_attempt_at INTEGER,created_at INTEGER,sent_at INTEGER,last_error TEXT,
            UNIQUE(driver_id,kind,expiry,milestone));
        ''')
        self.connection.execute("INSERT INTO drivers VALUES('d','approved','driver@example.org',1,?,NULL,NULL)", ((self.today+timedelta(days=7)).isoformat(),))
        owner=self
        class Adapter:
            def execute(self, query, args=()):
                return Cursor(owner.connection.execute(query.replace('%s','?').replace(' FOR UPDATE SKIP LOCKED',''), args))
        self.adapter=Adapter()
        @contextmanager
        def db():
            yield self.adapter
            self.connection.commit()
        self.patch=patch.object(server,'db',db); self.patch.start()
        self.config=patch.object(server,'recovery_email_configured',return_value=True); self.config.start()
    def tearDown(self):
        self.patch.stop(); self.config.stop(); self.connection.close()
    def queue(self): server.queue_expiry_reminders(self.adapter,self.today,100)
    def row(self): return self.adapter.execute('SELECT * FROM driver_expiry_notifications').fetchone()
    def test_duplicate_scan_and_send_only_once(self):
        self.queue(); self.queue()
        self.assertEqual(len(self.adapter.execute('SELECT * FROM driver_expiry_notifications').fetchall()),1)
        with patch.object(server,'send_expiry_email') as send:
            self.assertEqual(server.process_expiry_reminders(today=self.today,now=100),1)
            self.assertEqual(server.process_expiry_reminders(today=self.today,now=100),0)
            self.assertEqual(send.call_args.args[0],'driver@example.org')
            send.assert_called_once()
        self.assertEqual(self.row()['status'],'sent')
    def test_unverified_email_not_queued(self):
        self.adapter.execute('UPDATE drivers SET recovery_email_verified=0'); self.queue()
        self.assertIsNone(self.row())
    def test_renewed_document_cancels_old_reminder(self):
        self.queue(); self.adapter.execute('UPDATE drivers SET driving_licence_expiry=?',((self.today+timedelta(days=100)).isoformat(),))
        with patch.object(server,'send_expiry_email') as send:
            server.process_expiry_reminders(today=self.today,now=100); send.assert_not_called()
        self.assertEqual(self.row()['status'],'cancelled')
    def test_suspended_driver_cancels_reminder(self):
        self.queue(); self.adapter.execute("UPDATE drivers SET status='suspended'")
        with patch.object(server,'send_expiry_email') as send:
            server.process_expiry_reminders(today=self.today,now=100); send.assert_not_called()
        self.assertEqual(self.row()['status'],'cancelled')
    def test_retry_backoff_and_stop_after_five_failures(self):
        self.queue()
        with patch.object(server,'send_expiry_email',side_effect=server.ReminderNotSent('private SMTP detail')):
            for attempt in range(1,6):
                now=self.row()['next_attempt_at']
                server.process_expiry_reminders(today=self.today,now=now)
                self.assertEqual(self.row()['attempts'],attempt)
                self.assertNotIn('private',self.row()['last_error'])
        self.assertEqual(self.row()['status'],'failed')
    def test_expiry_day_is_still_valid_and_next_day_gets_expired_notice(self):
        driver={'status':'approved','driving_licence_expiry':self.today.isoformat()}
        self.assertEqual(server.due_expiry_reminders(driver,self.today)[0]['milestone'],1)
        self.assertEqual(server.due_expiry_reminders(driver,self.today+timedelta(days=1))[0]['milestone'],0)
    def test_downtime_sends_only_closest_milestone(self):
        driver={'status':'approved','insurance_expiry':(self.today+timedelta(days=4)).isoformat()}
        self.assertEqual([r['milestone'] for r in server.due_expiry_reminders(driver,self.today)],[7])
    def test_uncertain_delivery_is_not_automatically_retried(self):
        self.queue()
        def uncertain(*args):
            self.assertEqual(self.row()['status'],'sending')
            raise server.ReminderDeliveryUncertain('private detail')
        with patch.object(server,'send_expiry_email',side_effect=uncertain) as send:
            server.process_expiry_reminders(today=self.today,now=100)
            server.process_expiry_reminders(today=self.today,now=1000)
            send.assert_called_once()
        self.assertEqual(self.row()['status'],'uncertain')
    def test_worker_crash_after_claim_leaves_no_retryable_message(self):
        self.queue()
        with patch.object(server,'send_expiry_email',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):server.process_expiry_reminders(today=self.today,now=100)
        self.assertEqual(self.row()['status'],'sending')
        with patch.object(server,'send_expiry_email') as send:
            server.process_expiry_reminders(today=self.today,now=401);send.assert_not_called()
        self.assertEqual(self.row()['status'],'uncertain')
    def test_no_smtp_configuration_does_not_mark_sent(self):
        self.queue()
        with patch.object(server,'recovery_email_configured',return_value=False):
            self.assertEqual(server.process_expiry_reminders(today=self.today,now=100),0)
        self.assertEqual(self.row()['status'],'pending')

if __name__=='__main__': unittest.main()
