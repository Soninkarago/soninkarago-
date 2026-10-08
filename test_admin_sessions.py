import sqlite3,unittest,json,threading
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from contextlib import contextmanager
from unittest.mock import patch
import server

class AdminSessions(unittest.TestCase):
    def test_session_database_can_revoke_still_signed_admin_token(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        self.addCleanup(c.close)
        c.execute('CREATE TABLE admin_sessions(id TEXT,identity TEXT,expires_at INT,revoked BOOLEAN DEFAULT FALSE)')
        token=server.make_token('admin','Admin');payload=server.read_token_value(token)
        c.execute('INSERT INTO admin_sessions(id,identity,expires_at) VALUES(?,?,?)',(payload['session_id'],'Admin',payload['exp']))
        class Cursor:
            def __init__(self,c):self.c=c
            def fetchone(self):
                row=self.c.fetchone();return dict(row) if row else None
        class DB:
            def execute(self,q,args=()):return Cursor(c.execute(q.replace('%s','?'),args))
        @contextmanager
        def db():yield DB()
        app=object.__new__(server.App);app.headers={'Authorization':'Bearer '+token}
        with patch.object(server,'db',db):
            self.assertIsNotNone(app.auth())
            c.execute('UPDATE admin_sessions SET revoked=TRUE')
            self.assertIsNone(app.auth())
            c.execute('UPDATE admin_sessions SET revoked=FALSE,expires_at=0')
            self.assertIsNone(app.auth())
        self.assertLessEqual(payload['exp']-payload['issued_at'],1800)
    def test_logout_revokes_old_token_and_immediate_login_succeeds(self):
        c=sqlite3.connect(':memory:',check_same_thread=False);c.row_factory=sqlite3.Row
        c.executescript('CREATE TABLE admin_sessions(id TEXT PRIMARY KEY,identity TEXT,expires_at INT,revoked BOOLEAN DEFAULT FALSE); CREATE TABLE drivers(id TEXT,status TEXT,online BOOLEAN,compliance_verified BOOLEAN);')
        class Cursor:
            def __init__(self,c):self.c=c;self.rowcount=c.rowcount
            def fetchone(self):
                r=self.c.fetchone();return dict(r) if r else None
        class DB:
            def execute(self,q,args=()):return Cursor(c.execute(q.replace('%s','?'),args))
        @contextmanager
        def db():
            try:yield DB();c.commit()
            except:c.rollback();raise
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.App);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        def call(path,fields=None,token=None):
            headers={'Content-Type':'application/json','X-SoninkaraGo-App':'ios'}
            if token:headers['Authorization']='Bearer '+token
            req=Request(f'http://127.0.0.1:{http.server_port}/api'+path,data=json.dumps(fields or {}).encode(),headers=headers)
            try:r=urlopen(req,timeout=3)
            except HTTPError as e:r=e
            with r:return r.status,json.loads(r.read())
        try:
            with patch.object(server,'db',db),patch.object(server,'ADMIN_PASSWORD','local-test-only'),patch.object(server.App,'check_rate',return_value=True),patch.object(server,'audit_event'),patch.object(server.App,'log_message'):
                status,login=call('/login/admin',{'password':'local-test-only'});self.assertEqual(status,200);old=login['access_token']
                self.assertEqual(call('/admin/drivers/ABSENT/reject',token=old)[0],404)
                self.assertEqual(call('/logout',token=old)[0],200)
                self.assertEqual(call('/admin/drivers/ABSENT/reject',token=old)[0],401)
                status,login=call('/login/admin',{'password':'local-test-only'});self.assertEqual(status,200)
                self.assertNotEqual(login['access_token'],old)
                self.assertEqual(call('/admin/drivers/ABSENT/reject',token=login['access_token'])[0],404)
        finally:http.shutdown();http.server_close();thread.join();c.close()
    def test_financial_journal_cannot_fail_open(self):
        class Broken:
            def execute(self,*args):raise RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):server.payment_event_once(Broken(),'paytech','K','R','sale_complete',500,{})

if __name__=='__main__':unittest.main()
