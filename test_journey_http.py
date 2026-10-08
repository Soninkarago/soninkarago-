"""Synthetic HTTP journey on an isolated SQLite DB. No live customer, payment or photo."""
import base64,io,json,re,sqlite3,tempfile,threading,time,unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from PIL import Image
import server,booking_requests,journey_experience as journey,journey_support as support,journey_chat as chat

class JourneyHTTP(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'journey.db'
  columns=re.search(r'INSERT INTO rides\((.*?)\)\s*VALUES',Path(server.__file__).read_text(),re.S).group(1)
  with sqlite3.connect(self.path) as c:
   c.execute('CREATE TABLE rides('+','.join(n.strip()+' TEXT' for n in columns.split(','))+',driver_id TEXT,offered_driver_id TEXT,offer_expires_at BIGINT,driver_lat REAL,driver_lng REAL,driver_location_at BIGINT,eta_seconds INT,eta_calculated_at BIGINT,eta_driver_location_at BIGINT,eta_distance_meters INT)')
   for schema in journey.SCHEMAS+(support.SCHEMA,support.REQUEST_SCHEMA,chat.SCHEMA,booking_requests.SCHEMA):c.execute(schema)
   c.execute('CREATE TABLE support_requests(id TEXT PRIMARY KEY,ride_id TEXT,phone TEXT,category TEXT,message TEXT,status TEXT,created_at BIGINT)')
   c.execute('CREATE TABLE drivers(id TEXT PRIMARY KEY,name TEXT,phone TEXT,status TEXT,vehicle TEXT,vehicle_plate TEXT,balance INT,compliance_verified BOOLEAN,driving_licence_number TEXT,driving_licence_expiry TEXT,insurance_policy_number TEXT,insurance_expiry TEXT,registration_card_number TEXT)')
   c.execute("INSERT INTO drivers VALUES('D','Chauffeur fictif','770000001','approved','Moto-taxi','TEST-123',10000,TRUE,'P','2099-01-01','I','2099-01-01','C')")
  class Cursor:
   def __init__(self,c):self.c=c;self.rowcount=c.rowcount
   def fetchone(self):
    r=self.c.fetchone();return dict(r) if r is not None else None
   def fetchall(self):return [dict(r) for r in self.c.fetchall()]
  class DB:
   def __init__(self,c):self.c=c
   def execute(self,q,args=()):return Cursor(self.c.execute(q.replace('%s','?').replace(' FOR UPDATE',''),args))
  @contextmanager
  def db():
   c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
   try:yield DB(c);c.commit()
   except:c.rollback();raise
   finally:c.close()
  self.db=db
  def auth(app):return {'role':app.headers.get('X-Test-Role','driver'),'driver_id':app.headers.get('X-Test-Driver','D'),'name':'Chauffeur fictif'}
  def assign(conn,ride_id):conn.execute('UPDATE rides SET offered_driver_id=%s,offer_expires_at=%s WHERE id=%s',('D',int(time.time())+60,ride_id));return 'D'
  for p in (patch.object(server,'db',db),patch.object(server.App,'auth',auth),patch.object(server.App,'check_rate',return_value=True),patch.object(server.App,'log_message'),patch.object(server,'assign_next_driver',side_effect=assign),patch.object(server,'verify_local_quote',return_value={'pickup':'Moudéry','destination':'Bakel','fare':3000,'lat':14.7,'lng':-17.4,'route_polyline':'_p~iF~ps|U_ulLnnqC_mqNvxq`@','stops':[]}),patch.object(server,'audit_event')):p.start();self.addCleanup(p.stop)
  self.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.App);self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  self.addCleanup(self.stop)
 def stop(self):self.http.shutdown();self.http.server_close();self.thread.join()
 def call(self,path,data=None,role='driver',driver='D'):
  request=Request(f'http://127.0.0.1:{self.http.server_port}'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-Test-Role':role,'X-Test-Driver':driver})
  try:r=urlopen(request,timeout=5)
  except HTTPError as e:r=e
  with r:
   raw=r.read();return r.status,json.loads(raw) if 'application/json' in r.headers.get('Content-Type','') else raw.decode()
 def profile(self):
  out=io.BytesIO();Image.new('RGB',(100,100),'white').save(out,'PNG');photo=base64.b64encode(out.getvalue()).decode()
  status,_=self.call('/api/driver/profile',{'make':'Moto','model':'Test','color':'Blanc','plate':'TEST-123','portrait':photo,'vehicle_photo':photo});self.assertEqual(status,200)
  profile=self.call('/api/admin/drivers/D/profile',role='admin')[1]['profile']
  self.assertEqual(self.call('/api/admin/drivers/D/profile-review',{'status':'approved','updated_at':profile['updated_at']},role='admin')[0],200)
 def test_full_synthetic_journey_and_lost_responses(self):
  self.profile()
  payload={'request_key':'K'*43,'route_code':'local_moto','quote_token':'signed-test','pickup_code_enabled':True,'journey_features':True,'client_name':'Client fictif','phone':'770000000','payment':'Espèces','terms_accepted':True,'privacy_accepted':True}
  status,ride=self.call('/api/rides',payload);self.assertEqual(status,201);ref=ride['id'];token=ride['tracking_token'];url='/api/rides/'+ref
  status,replay=self.call('/api/rides',payload);self.assertEqual(status,200);self.assertEqual(replay['id'],ref)
  self.assertEqual(self.call(url+'/accept',{})[0],409) # old client cannot accept a protected course
  self.assertEqual(self.call(url+'/accept',{'pickup_code_supported':True})[0],200)
  self.assertEqual(self.call(url+'/complete',{})[0],409)
  self.assertEqual(self.call(url+'/arrive',{})[0],200);self.assertEqual(self.call(url+'/arrive',{})[0],200)
  status,state=self.call(url+'?token='+token);self.assertEqual(status,200);self.assertEqual(state['status'],'arriving');self.assertEqual(state['driver_profile']['plate'],'TEST-123');self.assertNotIn('licence',str(state));code=state['pickup_code']
  shared=self.call(url+'/share',{'tracking_token':token})[1]['url'].replace('https://soninkarago.sn','');self.assertEqual(self.call(shared)[0],200)
  message={'tracking_token':token,'message':'Je suis au rendez-vous','request_id':'M'*32}
  self.assertEqual(self.call(url+'/chat',message)[0],200);self.assertEqual(self.call(url+'/chat',message)[0],200);self.assertEqual(len(self.call(url+'/chat?token='+token)[1]),1)
  self.assertEqual(self.call(url+'/start',{'code':code},driver='OTHER')[0],403)
  self.assertEqual(self.call(url+'/start',{'code':code})[0],200);self.assertTrue(self.call(url+'/start',{'code':code})[1]['already_started'])
  self.assertEqual(self.call(url+'/location/driver',{'lat':14.7,'lng':-17.4})[0],200)
  self.assertEqual(self.call(url+'/complete',{})[0],200);self.assertEqual(self.call(shared)[0],410)
  self.assertEqual(self.call(url+'/rate',{'tracking_token':'wrong','stars':5})[0],401)
  rating={'tracking_token':token,'stars':5,'comment':'Test isolé'};self.assertEqual(self.call(url+'/rate',rating)[0],200);self.assertEqual(self.call(url+'/rate',rating)[0],200)
  request={'ride_id':ref,'tracking_token':token,'category':'app','message':'Demande de test isolée','request_id':'S'*32}
  first=self.call('/api/support',request)[1];second=self.call('/api/support',request)[1];self.assertEqual(first['request_id'],second['request_id'])
  self.assertEqual(self.call('/api/support',{**request,'message':'Autre demande de test'})[0],409)
  self.assertEqual(self.call('/api/admin/journey-support/'+first['request_id'],{'status':'resolved','reply':'Réponse de test isolée'},role='admin')[0],200)
  self.assertEqual(self.call(url+'/support?token='+token)[1][0]['status'],'resolved')
  with self.db() as conn:
   self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM rides').fetchone()['n'],1)
   self.assertEqual(conn.execute('SELECT balance FROM drivers WHERE id=%s',('D',)).fetchone()['balance'],9700)
   self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM journey_ratings').fetchone()['n'],1)
