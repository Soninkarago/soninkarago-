import unittest,sqlite3,base64,io,time,json
from contextlib import contextmanager
from unittest.mock import patch
from PIL import Image
import journey_experience as j
class JourneyExperience(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row;self.addCleanup(self.c.close)
  for schema in j.SCHEMAS:self.c.execute(schema)
  self.c.executescript('''CREATE TABLE rides(id TEXT PRIMARY KEY,tracking_token TEXT,status TEXT,driver_id TEXT,vehicle TEXT,driver_lat REAL,driver_lng REAL,driver_location_at INTEGER,driver_name TEXT);CREATE TABLE drivers(id TEXT,vehicle_plate TEXT);INSERT INTO drivers VALUES('D','DK123AA');INSERT INTO rides VALUES('R','private-token','accepted','D','Voiture taxi',14.7,-17.4,0,'Test');''')
  outer=self
  class Cursor:
   def __init__(self,c):self.c=c;self.rowcount=c.rowcount
   def fetchall(self):return [dict(r) for r in self.c.fetchall()]
   def fetchone(self):
    r=self.c.fetchone();return dict(r) if r else None
  class DB:
   def execute(self,q,args=()):return Cursor(outer.c.execute(q.replace('%s','?').replace(' FOR UPDATE',''),args))
  @contextmanager
  def db():yield DB();outer.c.commit()
  self.db=db;self.user={'role':'driver','driver_id':'D'};self.response=None
  class App:
   def auth(self):return outer.user
   def check_rate(self,*args):return True
   def sendj(self,data,status=200):outer.response=(status,data)
   def sendh(self,data,status=200):outer.response=(status,data)
  self.app=App()
 def post(self,action,data):
  self.assertTrue(j.handle_post(self.app,'/api/rides/R/'+action,data,self.db));return self.response
 def test_trip_pin_wrong_owner_attempt_limit_and_success(self):
  with self.db() as conn:code=j.ensure_code(conn,'R')['code']
  self.user={'role':'driver','driver_id':'OTHER'};self.assertEqual(self.post('start',{'code':code})[0],403)
  self.user={'role':'driver','driver_id':'D'}
  for _ in range(5):self.assertEqual(self.post('start',{'code':'invalid'})[0],400)
  self.assertEqual(self.post('start',{'code':code})[0],429)
  self.c.execute('UPDATE journey_codes SET locked_until=0,attempts=0')
  self.assertEqual(self.post('start',{'code':code})[0],200)
  self.assertEqual(self.c.execute('SELECT status FROM rides').fetchone()[0],'in_progress')
  self.assertEqual(self.post('start',{'code':code})[1]['already_started'],True)
 def test_completion_only_after_code_for_modern_booking(self):
  with self.db() as conn:j.ensure_code(conn,'R')
  sql="UPDATE rides SET status='completed' WHERE id='R' AND (NOT EXISTS(SELECT 1 FROM journey_codes c WHERE c.ride_id=rides.id) OR EXISTS(SELECT 1 FROM journey_codes c WHERE c.ride_id=rides.id AND c.verified_at IS NOT NULL))"
  self.assertEqual(self.c.execute(sql).rowcount,0)
  self.c.execute('UPDATE journey_codes SET verified_at=1');self.assertEqual(self.c.execute(sql).rowcount,1)
 def test_sharing_no_private_token_revocable_and_expiring(self):
  self.assertEqual(self.post('share',{'tracking_token':'wrong'})[0],401)
  status,data=self.post('share',{'tracking_token':'private-token'});self.assertEqual(status,200);self.assertNotIn('private-token',data['url'])
  path=data['url'].replace('https://soninkarago.sn','');j.handle_get(self.app,path,self.db);self.assertEqual(self.response[0],200);self.assertNotIn('private-token',self.response[1])
  self.post('revoke-share',{'tracking_token':'private-token'});j.handle_get(self.app,path,self.db);self.assertEqual(self.response[0],410)
  _,data=self.post('share',{'tracking_token':'private-token'});path=data['url'].replace('https://soninkarago.sn','');self.c.execute('UPDATE journey_shares SET expires_at=0');j.handle_get(self.app,path,self.db);self.assertEqual(self.response[0],410)
 def test_completed_trip_ends_share_and_rating_authorization(self):
  _,data=self.post('share',{'tracking_token':'private-token'});path=data['url'].replace('https://soninkarago.sn','')
  self.assertEqual(self.post('rate',{'tracking_token':'private-token','stars':5})[0],409)
  self.c.execute("UPDATE rides SET status='completed'");j.handle_get(self.app,path,self.db);self.assertEqual(self.response[0],410)
  self.assertEqual(self.post('rate',{'tracking_token':'wrong','stars':5})[0],401)
  for stars in (0,6,True,2.5):self.assertEqual(self.post('rate',{'tracking_token':'private-token','stars':stars})[0],400)
  self.assertEqual(self.post('rate',{'tracking_token':'private-token','stars':5})[0],200)
  self.post('rate',{'tracking_token':'private-token','stars':4});self.assertEqual(self.c.execute('SELECT COUNT(*) FROM journey_ratings').fetchone()[0],1)
 def test_photo_is_sanitized_and_pending_not_visible(self):
  raw=io.BytesIO();Image.new('RGB',(600,600),'white').save(raw,'PNG');encoded=base64.b64encode(raw.getvalue()).decode()
  clean=j.clean_photo(encoded);self.assertTrue(clean.startswith('data:image/jpeg;base64,'))
  with Image.open(io.BytesIO(base64.b64decode(clean.split(',')[1]))) as p:self.assertEqual(p.size,(480,480));self.assertFalse(p.getexif())
  with self.assertRaises(ValueError):j.clean_photo(base64.b64encode(b'<script>x</script>').decode())
  profile={'make':'Toyota','model':'Yaris','color':'Blanc','plate':'DK123AA','portrait':encoded,'vehicle_photo':encoded}
  self.assertTrue(j.handle_post(self.app,'/api/driver/profile',profile,self.db));self.assertEqual(self.response[0],200)
  with self.db() as conn:self.assertIsNone(j.trust_fields(conn,dict(self.c.execute('SELECT * FROM rides').fetchone()))['driver_profile'])
  self.user={'role':'admin'};j.handle_get(self.app,'/api/admin/drivers/D/profile',self.db);version=self.response[1]['profile']['updated_at']
  j.handle_post(self.app,'/api/admin/drivers/D/profile-review',{'status':'approved','updated_at':version-1},self.db);self.assertEqual(self.response[0],409)
  j.handle_post(self.app,'/api/admin/drivers/D/profile-review',{'status':'approved','updated_at':version},self.db);self.assertEqual(self.response[0],200)
  with self.db() as conn:self.assertEqual(j.trust_fields(conn,dict(self.c.execute('SELECT * FROM rides').fetchone()))['driver_profile']['plate'],'DK123AA')
 def test_legacy_tracking_does_not_create_code(self):
  with self.db() as conn:self.assertIsNone(j.trust_fields(conn,dict(self.c.execute('SELECT * FROM rides').fetchone()))['pickup_code'])
  self.assertEqual(self.c.execute('SELECT COUNT(*) FROM journey_codes').fetchone()[0],0)
 def test_only_fresh_shared_location_and_escape_names(self):
  self.c.execute("UPDATE rides SET driver_name='<script>bad</script>',driver_location_at=?",(int(time.time())-61,))
  _,data=self.post('share',{'tracking_token':'private-token'});j.handle_get(self.app,data['url'].replace('https://soninkarago.sn',''),self.db)
  self.assertNotIn('<script>',self.response[1]);self.assertNotIn('maps?q=',self.response[1]);self.assertIn('&lt;script&gt;',self.response[1])
 def test_changed_plate_hides_old_profile_and_blocks_review(self):
  raw=io.BytesIO();Image.new('RGB',(100,100),'white').save(raw,'PNG');encoded=base64.b64encode(raw.getvalue()).decode()
  profile={'make':'Toyota','model':'Test','color':'Blanc','plate':'DK123AA','portrait':encoded,'vehicle_photo':encoded}
  j.handle_post(self.app,'/api/driver/profile',profile,self.db)
  version=self.c.execute('SELECT updated_at FROM driver_profiles').fetchone()[0]
  self.c.execute("UPDATE drivers SET vehicle_plate='NEW-123'");self.user={'role':'admin'}
  j.handle_post(self.app,'/api/admin/drivers/D/profile-review',{'status':'approved','updated_at':version},self.db);self.assertEqual(self.response[0],409)
  self.c.execute("UPDATE driver_profiles SET status='approved'")
  with self.db() as conn:self.assertIsNone(j.trust_fields(conn,dict(self.c.execute('SELECT * FROM rides').fetchone()))['driver_profile'])
 def test_profile_versions_change_even_with_frozen_clock(self):
  raw=io.BytesIO();Image.new('RGB',(100,100),'white').save(raw,'PNG');encoded=base64.b64encode(raw.getvalue()).decode()
  profile={'make':'Toyota','model':'Test','color':'Blanc','plate':'DK123AA','portrait':encoded,'vehicle_photo':encoded}
  with patch.object(j.time,'time',return_value=100):
   j.handle_post(self.app,'/api/driver/profile',profile,self.db);first=self.c.execute('SELECT updated_at FROM driver_profiles').fetchone()[0]
   j.handle_post(self.app,'/api/driver/profile',profile,self.db);second=self.c.execute('SELECT updated_at FROM driver_profiles').fetchone()[0]
  self.assertGreater(second,first)
if __name__=='__main__':unittest.main()
