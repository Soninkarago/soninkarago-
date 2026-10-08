import sqlite3, unittest, json, threading
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from contextlib import contextmanager
from unittest.mock import patch
import server

class RestrictedCourse(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript("CREATE TABLE drivers(id TEXT,status TEXT,pin_reset_at REAL); CREATE TABLE rides(id TEXT,driver_id TEXT,status TEXT); INSERT INTO drivers VALUES('D','suspended',0); INSERT INTO rides VALUES('OWN','D','in_progress'); INSERT INTO rides VALUES('OTHER','X','in_progress');")
        self.addCleanup(self.c.close)
        outer = self
        class Cursor:
            def __init__(self, c): self.c=c
            def fetchone(self):
                r=self.c.fetchone();return dict(r) if r is not None else None
        class DB:
            def execute(self,q,args=()): return Cursor(outer.c.execute(q.replace('%s','?'),args))
        @contextmanager
        def db(): yield DB()
        for p in (patch.object(server,'db',db),patch.object(server,'read_token',return_value={'role':'driver','driver_id':'D','issued_at':100})):
            p.start();self.addCleanup(p.stop)
        self.app=object.__new__(server.App);self.app.headers={'Authorization':'test'}
    def auth(self,path,method='POST'):
        self.app.path=path;self.app.command=method;return self.app.auth()
    def test_restricted_driver_can_only_close_own_active_assignment(self):
        for action in ('complete','confirm-deposit','confirm-balance','location/driver'):
            self.assertTrue(self.auth('/api/rides/OWN/'+action)['restricted'])
            self.assertIsNone(self.auth('/api/rides/OTHER/'+action))
    def test_restriction_blocks_new_work_money_and_other_routes(self):
        for path in ('/api/rides/OWN/accept','/api/driver/location','/api/driver/recharge','/api/driver/email','/api/admin/drivers'):
            self.assertIsNone(self.auth(path))
    def test_restriction_expires_with_course_and_pin_reset_revokes_everything(self):
        self.assertTrue(self.auth('/api/rides','GET')['restricted'])
        self.c.execute("UPDATE rides SET status='completed' WHERE id='OWN'")
        self.assertIsNone(self.auth('/api/rides','GET'))
        self.c.execute("UPDATE rides SET status='in_progress' WHERE id='OWN'")
        self.c.execute("UPDATE drivers SET pin_reset_at=101")
        self.assertIsNone(self.auth('/api/rides/OWN/complete'))

class MinicarClosure(unittest.TestCase):
    def test_only_valid_minicar_payment_and_completion_transitions_commit(self):
        c=sqlite3.connect(':memory:',check_same_thread=False);c.row_factory=sqlite3.Row
        c.execute('CREATE TABLE rides(id TEXT,driver_id TEXT,status TEXT,vehicle TEXT,payment_status TEXT,balance_paid_at INTEGER,balance_due INTEGER)')
        c.execute("INSERT INTO rides VALUES('R','D','cancelled','Minicar 14 places','deposit_paid',NULL,500)")
        class Cursor:
            def __init__(self,c):self.c=c;self.rowcount=c.rowcount
            def fetchone(self):
                r=self.c.fetchone();return dict(r) if r else None
        class DB:
            def execute(self,q,args=()):return Cursor(c.execute(q.replace('%s','?').replace(' FOR UPDATE',''),args))
        @contextmanager
        def db():
            try:yield DB();c.commit()
            except:c.rollback();raise
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.App)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        def call(action):
            request=Request(f'http://127.0.0.1:{http.server_port}/api/rides/R/'+action,data=b'{}',headers={'Content-Type':'application/json'})
            try:response=urlopen(request,timeout=3)
            except HTTPError as error:response=error
            with response:return response.status
        try:
            with patch.object(server,'db',db),patch.object(server.App,'auth',return_value={'role':'driver','driver_id':'D'}),patch.object(server.App,'log_message'):
                self.assertEqual(call('confirm-balance'),409)
                c.execute("UPDATE rides SET payment_status='fully_paid'")
                self.assertEqual(call('complete'),409)
                c.execute("UPDATE rides SET status='deposit_paid',payment_status='deposit_paid'")
                self.assertEqual(call('confirm-balance'),200)
                self.assertEqual(call('confirm-balance'),409)
                self.assertEqual(call('complete'),200)
                self.assertEqual(call('complete'),409)
                self.assertEqual(c.execute('SELECT status FROM rides').fetchone()[0],'completed')
        finally:http.shutdown();http.server_close();thread.join();c.close()

if __name__=='__main__': unittest.main()
