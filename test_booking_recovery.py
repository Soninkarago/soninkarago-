"""Real handler and durable unique-key storage, including lost responses."""
import json,re,sqlite3,tempfile,threading,unittest
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import server as app
import booking_requests as bookings

class Cursor:
 def __init__(self,c):self.c=c;self.rowcount=c.rowcount
 def fetchone(self):
  r=self.c.fetchone();return dict(r) if r is not None else None
class Connection:
 def __init__(self,c):self.c=c
 def execute(self,q,args=()):return Cursor(self.c.execute(q.replace('%s','?'),args))
class BookingHTTP(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.http=app.ThreadingHTTPServer(('127.0.0.1',0),app.App)
  cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.thread.join()
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'test.db'
  columns=re.search(r'INSERT INTO rides\((.*?)\)\s*VALUES',Path(app.__file__).read_text(),re.S).group(1)
  with sqlite3.connect(self.path) as c:
   c.execute('CREATE TABLE rides('+','.join(n.strip()+' TEXT' for n in columns.split(','))+')');c.execute(bookings.SCHEMA)
  @contextmanager
  def db():
   c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
   try:yield Connection(c);c.commit()
   except:c.rollback();raise
   finally:c.close()
  self.db=db
  self.dispatch=patch.object(app,'assign_next_driver',return_value=None).start();self.payment=patch.object(app,'request_paytech_payment',return_value='https://paytech.sn/test').start()
  for p in [patch.object(app,'db',db),patch.object(app.App,'check_rate',return_value=True),patch.object(app.App,'log_message')]:p.start();self.addCleanup(p.stop)
  self.addCleanup(patch.stopall)
  self.payload={'request_key':'K'*43,'route_code':'minicar_dakar_touba','departure_date':'2099-01-01','departure_time':'10:00','meeting_point':'Test','passenger_count':1,'client_name':'Test','phone':'770000001','payment':'Espèces','terms_accepted':True,'privacy_accepted':True}
 def call(self,body):
  req=Request(f'http://127.0.0.1:{self.http.server_port}/api/rides',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
  try:r=urlopen(req,timeout=15)
  except HTTPError as e:r=e
  with r:return r.status,json.loads(r.read())
 def count(self):
  with sqlite3.connect(self.path) as c:return c.execute('SELECT COUNT(*) FROM rides').fetchone()[0]
 def test_retry_after_lost_response_returns_same_reference(self):
  status,first=self.call(self.payload);status2,retry=self.call(self.payload)
  self.assertEqual((status,status2),(201,200));self.assertEqual(first['id'],retry['id']);self.assertEqual(first['tracking_token'],retry['tracking_token']);self.assertEqual(self.count(),1);self.dispatch.assert_called_once()
 def test_concurrent_requests_create_only_one_ride(self):
  with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(self.call,[self.payload]*8))
  self.assertEqual({r[1]['id'] for r in results}, {results[0][1]['id']});self.assertEqual(sum(r[0]==201 for r in results),1);self.assertEqual(self.count(),1);self.dispatch.assert_called_once()
 def test_same_key_different_payload_is_rejected(self):
  self.call(self.payload);self.assertEqual(self.call({**self.payload,'passenger_count':2})[0],409);self.assertEqual(self.count(),1)
 def test_retry_never_creates_second_payment_session(self):
  body={**self.payload,'payment':'Wave'};first=self.call(body)[1];retry=self.call(body)[1]
  self.assertEqual(first['payment_url'],retry['payment_url']);self.payment.assert_called_once();self.assertEqual(self.count(),1)
 def test_committed_initial_response_is_recoverable_before_provider_finishes(self):
  body={**self.payload,'payment':'Wave'};request=bookings.identity(body)
  response={'id':'EXISTING','tracking_token':'secret','payment_initialization_pending':True}
  with self.db() as c:bookings.claim(c,request,response)
  result=self.call(body);self.assertEqual(result[0],200);self.assertEqual(result[1]['id'],'EXISTING');self.payment.assert_not_called()
 def test_retry_works_after_quote_expires(self):
  body={**self.payload,'route_code':'urban_car_dakar','quote_token':'old'}
  quote={'zone':'dakar','pickup':'A','destination':'B','fare':1000,'lat':14.7,'lng':-17.4}
  # A committed request is retrieved before any quote revalidation.
  with self.db() as c:bookings.claim(c,bookings.identity(body),{'id':'R','tracking_token':'T'})
  with patch.object(app,'verify_dakar_quote',side_effect=ValueError('Expired')) as verifier:
   self.assertEqual(self.call(body)[1]['id'],'R');verifier.assert_not_called()
 def test_key_hash_does_not_store_bearer_secret(self):
  self.call(self.payload)
  with sqlite3.connect(self.path) as c:row=c.execute('SELECT key_hash,response_json FROM booking_requests').fetchone()
  self.assertNotIn(self.payload['request_key'],''.join(row));self.assertEqual(len(row[0]),64)
