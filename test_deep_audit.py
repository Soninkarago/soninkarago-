"""Security regressions through the real HTTP handler; no production services."""
import hashlib,hmac,json,socket,threading,unittest,subprocess,sys
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import server as app
import document_ocr as ocr

class Rows:
    rowcount=1
    def __init__(self,rows=()): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)

class AuditHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.App)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def setUp(self):
        self.statements=[];self.rows=[]
        outer=self
        class DB:
            def execute(self,q,args=()):outer.statements.append((q,args));return Rows(outer.rows if q.lstrip().startswith('SELECT') else [])
        @contextmanager
        def db():yield DB()
        for p in [patch.object(app,'db',db),patch.object(app.App,'check_rate',return_value=True),patch.object(app.App,'auth',return_value={'role':'admin','driver_id':'D'}),patch.object(app,'audit_event'),patch.object(app.App,'log_message')]:p.start();self.addCleanup(p.stop)
    def call(self,path,fields):
        req=Request(f'http://127.0.0.1:{self.server.server_port}/api'+path,data=json.dumps(fields).encode(),headers={'Content-Type':'application/json'})
        try:r=urlopen(req,timeout=3)
        except HTTPError as e:r=e
        with r:return r.status,json.loads(r.read())
    def test_missing_or_partial_compliance_checks_never_write(self):
        for checklist in [None,{},dict.fromkeys(app.COMPLIANCE_CHECKS,False),dict.fromkeys(app.COMPLIANCE_CHECKS,'true')]:
            self.assertEqual(self.call('/admin/drivers/D/verify-docs',{'checklist':checklist})[0],400)
        self.assertEqual(self.statements,[])
    def test_consent_not_fabricated(self):
        for body in [{},{'terms_accepted':True},{'terms_accepted':True,'privacy_accepted':'true'}]:self.assertEqual(self.call('/rides',body)[0],400)
        self.assertEqual(self.statements,[])
    def test_invalid_departures_and_fractional_passengers_rejected(self):
        fields={'route_code':'minicar_dakar_touba','terms_accepted':True,'privacy_accepted':True,'meeting_point':'Test'}
        for day,hour,count in [('2027-02-30','10:00',1),('2000-01-01','10:00',1),('2099-01-01','24:01',1),('2099-01-01','10:00',1.9),('2099-01-01','10:00',15)]:
            self.assertEqual(self.call('/rides',{**fields,'departure_date':day,'departure_time':hour,'passenger_count':count})[0],400)
        self.assertEqual(self.statements,[])
    def test_recharge_does_not_truncate_or_overflow(self):
        with patch.object(app.App,'auth',return_value={'role':'driver','driver_id':'D'}):
            for amount in [500.5,True,'1e100','NaN',-500]:self.assertEqual(self.call('/driver/recharge',{'amount':amount})[0],400)
        self.assertEqual(self.statements,[])
    def test_signed_pickups_and_terminal_rides_cannot_move(self):
        for code,status in [('local_moto','searching'),('urban_car_thies','accepted'),('dakar_car','accepted'),('minicar_dakar_touba','cancelled'),('minicar_dakar_touba','completed')]:
            self.rows=[{'status':status,'route_code':code,'tracking_token':'private'}]
            self.assertEqual(self.call('/rides/R/location/client',{'lat':14.8,'lng':-17.4,'tracking_token':'private'})[0],409)
        self.assertFalse(any(q.startswith('UPDATE') for q,_ in self.statements))
    def test_both_login_routes_share_normalized_account_limit(self):
        accounts={}
        def rate(key,limit,window):
            accounts[key]=accounts.get(key,0)+1;return accounts[key]<=limit
        with patch.object(app,'allow_request_shared',side_effect=rate):
            for i in range(9):
                status,_=self.call('/login/driver' if i%2 else '/driver/application/login',{'phone':'+221770000001' if i%2 else '770000001','pin':'0000'})
                self.assertEqual(status,401 if i<8 else 429)
        self.assertEqual(len(accounts),1)
    def test_cash_rides_do_not_raise_false_unpaid_anomaly(self):
        self.rows=[{'id':'CASH','payment':'Espèces','status':'searching','payment_status':'unpaid'}, {'id':'WAVE','payment':'Wave','status':'searching','payment_status':'unpaid'}]
        status,r=self.call('/admin/payments/reconciliation',{})
        self.assertEqual(status,200);self.assertEqual(r['anomalies'],[{'type':'ride_searching_unpaid','id':'WAVE'}])
    def test_late_failed_ride_payment_requires_reconciliation(self):
        self.rows=[{'id':'R','fare':1000,'deposit_amount':0,'status':'payment_failed'}]
        fields={'ref_command':'R','item_price':'1000','type_event':'sale_complete','currency':'XOF','env':'prod'}
        fields['hmac_compute']=hmac.new(b'secret',b'1000|R|key',hashlib.sha256).hexdigest()
        with patch.object(app,'PAYTECH_API_KEY','key'),patch.object(app,'PAYTECH_API_SECRET','secret'),patch.object(app,'PAYTECH_ENV','prod'):
            self.assertEqual(self.call('/paytech/ipn',fields)[0],409)
        self.assertFalse(any(q.lstrip().startswith('UPDATE') for q,_ in self.statements))

class AuditBoundaries(unittest.TestCase):
    def test_only_https_paytech_redirects(self):
        self.assertEqual(app.verified_paytech_redirect('https://paytech.sn/payment/test'),'https://paytech.sn/payment/test')
        for url in ['javascript:alert(1)','http://paytech.sn/a','https://paytech.sn.evil.test/a','https://evil.test/paytech.sn','https://user:secret@paytech.sn/a','https://paytech.sn:8443/a']:
            with self.assertRaises((ValueError,RuntimeError)):app.verified_paytech_redirect(url)
    def test_real_departure_and_explicit_checklist(self):
        now=datetime(2026,10,8,12,tzinfo=timezone.utc)
        self.assertEqual(app.validate_minicar_departure('2026-10-08','13:00',14,now),14)
        self.assertEqual(app.validate_compliance_checklist(dict.fromkeys(app.COMPLIANCE_CHECKS,True)),dict.fromkeys(app.COMPLIANCE_CHECKS,True))
    def test_ocr_timeout_kills_worker_and_removes_document(self):
        original=subprocess.Popen;workers=[];paths=[]
        def slow(command,**kwargs):
            paths.append(Path(command[3]));p=original([sys.executable,'-c','import time; time.sleep(60)'],**kwargs);workers.append(p);return p
        with patch.object(ocr.subprocess,'Popen',side_effect=slow),patch.object(ocr,'WORKER_TIMEOUT',.1):
            with self.assertRaises(RuntimeError):ocr.extract(b'fake','image/png')
        self.assertLess(workers[0].returncode,0);self.assertFalse(paths[0].exists())
        self.assertTrue(ocr.LOCK.acquire(blocking=False));ocr.LOCK.release()
    def test_http_concurrency_cap_rejects_excess_and_recovers(self):
        entered=threading.Event();release=threading.Event();lock=threading.Lock();count=[0]
        class Slow(app.App):
            def log_message(self,*args):pass
            def do_GET(self):
                with lock:
                    count[0]+=1
                    if count[0]==16:entered.set()
                release.wait(3);self.sendj({'ok':True})
        server=app.BoundedHTTPServer(('127.0.0.1',0),Slow)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();clients=[]
        try:
            for _ in range(16):
                s=socket.create_connection(server.server_address,timeout=2);s.sendall(b'GET / HTTP/1.0\r\n\r\n');clients.append(s)
            self.assertTrue(entered.wait(2))
            s=socket.create_connection(server.server_address,timeout=2);s.sendall(b'GET / HTTP/1.0\r\n\r\n');self.assertIn(b'503',s.recv(1024));s.close()
            release.set()
            for s in clients:
                self.assertIn(b'200',s.recv(1024));s.close()
        finally:
            release.set();server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
