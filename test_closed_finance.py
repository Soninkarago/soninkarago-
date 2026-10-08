import hashlib,hmac,json,sqlite3,threading,unittest
from contextlib import contextmanager
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import server
import closed_finance

class ClosedFinance(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:',check_same_thread=False);self.c.row_factory=sqlite3.Row
        self.c.executescript("""PRAGMA foreign_keys=ON;
        CREATE TABLE drivers(id TEXT PRIMARY KEY,balance INT,pin_hash TEXT,pin_salt TEXT,phone TEXT,name TEXT,status TEXT);
        CREATE TABLE driver_documents(driver_id TEXT REFERENCES drivers(id) ON DELETE CASCADE,content TEXT);
        CREATE TABLE rides(id TEXT,driver_id TEXT,driver_name TEXT,status TEXT,fare INT,fee INT,payment TEXT,tracking_token TEXT,client_lat REAL);
        CREATE TABLE driver_recharges(id TEXT PRIMARY KEY,driver_id TEXT REFERENCES drivers(id),amount INT,payment TEXT,status TEXT,created_at INT,paid_at INT);
        """)
        for schema in closed_finance.SCHEMAS:self.c.execute(schema)
        digest,salt=server.hash_pin('1234')
        self.c.execute('INSERT INTO drivers VALUES(?,?,?,?,?,?,?)',('D',700,digest,salt,'770000001','Private name','approved'))
        self.c.execute("INSERT INTO driver_documents VALUES('D','private-document')")
        self.c.execute("INSERT INTO rides VALUES('R','D','Private name','completed',1000,100,'Espèces','private-token',14.7)")
        self.c.execute("INSERT INTO driver_recharges VALUES('RECH-R','D',500,'wave','pending',100,NULL)");self.c.commit()
        outer=self
        class Cursor:
            def __init__(self,c):self.c=c;self.rowcount=c.rowcount
            def fetchone(self):
                r=self.c.fetchone();return dict(r) if r else None
            def fetchall(self):return [dict(r) for r in self.c.fetchall()]
        class DB:
            def execute(self,q,args=()):return Cursor(outer.c.execute(q.replace('%s','?').replace(' FOR UPDATE',''),args))
        @contextmanager
        def db():
            try:yield DB();outer.c.commit()
            except:outer.c.rollback();raise
        self.db=db
        self.audit=patch.object(server,'audit_event').start();self.addCleanup(patch.stopall)
        for p in (patch.object(server,'db',db),patch.object(server.App,'auth',return_value={'role':'driver','driver_id':'D'}),patch.object(server.App,'check_rate',return_value=True),patch.object(server.App,'log_message')):p.start()
        self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.App);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.c.close()
    def call(self,path,body):
        request=Request(f'http://127.0.0.1:{self.http.server_port}/api'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        try:r=urlopen(request,timeout=3)
        except HTTPError as error:r=error
        with r:return r.status,json.loads(r.read())
    def test_delete_preserves_balance_and_payment_references_but_no_profile_secrets(self):
        status,result=self.call('/driver/account/delete',{'pin':'1234'})
        self.assertEqual(status,200);self.assertTrue(result['financial_review_required'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM drivers').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM driver_documents').fetchone()[0],0)
        row=dict(self.c.execute('SELECT * FROM closed_driver_finance').fetchone())
        self.assertEqual(row['balance'],700);self.assertEqual(row['review_status'],'needs_review')
        self.assertEqual(self.c.execute('SELECT amount FROM closed_driver_recharges').fetchone()[0],500)
        for private in ('Private name','770000001','private-token','private-document','client_lat','pin_hash'):
            self.assertNotIn(private,json.dumps(row))
    def test_bad_pin_or_active_course_leaves_everything_intact(self):
        self.assertEqual(self.call('/driver/account/delete',{'pin':'9999'})[0],401)
        self.c.execute("UPDATE rides SET status='in_progress'")
        self.assertEqual(self.call('/driver/account/delete',{'pin':'1234'})[0],409)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM closed_driver_finance').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT balance FROM drivers').fetchone()[0],700)
    def test_suspended_application_deletion_also_preserves_finances(self):
        self.c.execute("UPDATE drivers SET status='suspended'")
        with patch.object(server.App,'auth',return_value={'role':'driver_application','driver_id':'D'}):
            self.assertEqual(self.call('/driver/application/delete',{'pin':'1234'})[0],200)
        self.assertEqual(self.c.execute('SELECT balance FROM closed_driver_finance').fetchone()[0],700)
    def test_payment_after_account_deletion_is_a_manual_incident_not_lost_or_credited(self):
        self.call('/driver/account/delete',{'pin':'1234'})
        body={'ref_command':'RECH-R','item_price':'500','type_event':'sale_complete','currency':'XOF','env':'prod'}
        body['hmac_compute']=hmac.new(b'secret',b'500|RECH-R|key',hashlib.sha256).hexdigest()
        with patch.object(server,'PAYTECH_API_KEY','key'),patch.object(server,'PAYTECH_API_SECRET','secret'),patch.object(server,'PAYTECH_ENV','prod'):
            self.assertEqual(self.call('/paytech/ipn',body)[0],409)
        self.assertEqual(self.c.execute('SELECT balance FROM closed_driver_finance').fetchone()[0],700)
        self.assertEqual(self.audit.call_args.args[3:5],('payment.late','closed_recharge'))
    def test_old_paid_callback_replay_does_not_create_false_settlement_incident(self):
        self.c.execute('UPDATE drivers SET balance=0')
        self.c.execute("UPDATE driver_recharges SET status='paid',paid_at=100")
        self.assertFalse(self.call('/driver/account/delete',{'pin':'1234'})[1]['financial_review_required'])
        body={'ref_command':'RECH-R','item_price':'500','type_event':'sale_complete','currency':'XOF','env':'prod'}
        body['hmac_compute']=hmac.new(b'secret',b'500|RECH-R|key',hashlib.sha256).hexdigest()
        with patch.object(server,'PAYTECH_API_KEY','key'),patch.object(server,'PAYTECH_API_SECRET','secret'),patch.object(server,'PAYTECH_ENV','prod'):
            status,result=self.call('/paytech/ipn',body)
        self.assertEqual(status,200);self.assertTrue(result['duplicate'])
        self.assertEqual(self.c.execute('SELECT review_status FROM closed_driver_finance').fetchone()[0],'archived')
    def test_preservation_and_deletion_roll_back_together(self):
        with self.assertRaises(RuntimeError):
            with self.db() as conn:
                driver=conn.execute('SELECT * FROM drivers').fetchone();closed_finance.preserve(conn,driver)
                conn.execute("DELETE FROM driver_recharges WHERE driver_id='D'")
                raise RuntimeError('simulated interrupted deletion')
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM closed_driver_finance').fetchone()[0],0)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM driver_recharges').fetchone()[0],1)
        self.assertEqual(self.c.execute('SELECT balance FROM drivers').fetchone()[0],700)

if __name__=='__main__':unittest.main()
