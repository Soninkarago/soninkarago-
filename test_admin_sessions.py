import sqlite3,unittest
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
    def test_financial_journal_cannot_fail_open(self):
        class Broken:
            def execute(self,*args):raise RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):server.payment_event_once(Broken(),'paytech','K','R','sale_complete',500,{})

if __name__=='__main__':unittest.main()
