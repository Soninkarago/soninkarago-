import unittest,time,json
import test_journey_experience as fixtures
import journey_support as support,journey_chat as chat
class SupportAndChat(unittest.TestCase):
 def setUp(self):
  fixtures.JourneyExperience.setUp(self)
  self.c.execute(support.SCHEMA);self.c.execute(chat.SCHEMA)
  self.c.execute('CREATE TABLE support_requests(id TEXT PRIMARY KEY,ride_id TEXT,phone TEXT,category TEXT,message TEXT,status TEXT,created_at BIGINT)')
  self.c.execute("INSERT INTO support_requests VALUES('SUP-TEST','R','770000001','Chauffeur','Question pour la course','open',1)")
  self.app.path='/api/rides/R/support?token=private-token'
 def test_support_role_and_passenger_course_scope(self):
  support.handle_get(self.app,'/api/rides/R/support',self.db);self.assertEqual(self.response[0],200);self.assertEqual(self.response[1][0]['id'],'SUP-TEST')
  self.app.path='/api/rides/R/support?token=bad';support.handle_get(self.app,'/api/rides/R/support',self.db);self.assertEqual(self.response[0],401)
  support.handle_post(self.app,'/api/admin/journey-support/SUP-TEST',{'status':'resolved','reply':'Une réponse utile'},self.db);self.assertEqual(self.response[0],401)
  self.user={'role':'admin'};support.handle_post(self.app,'/api/admin/journey-support/SUP-TEST',{'status':'resolved','reply':'Une réponse utile'},self.db);self.assertEqual(self.response[0],200)
  self.app.path='/api/rides/R/support?token=private-token';support.handle_get(self.app,'/api/rides/R/support',self.db);self.assertEqual(self.response[1][0]['reply'],'Une réponse utile');self.assertEqual(self.response[1][0]['status'],'resolved')
 def test_chat_no_other_driver_and_no_double_on_retry(self):
  data={'tracking_token':'private-token','message':'Je suis au point de départ','request_id':'A'*32}
  chat.handle_post(self.app,'/api/rides/R/chat',data,self.db);self.assertEqual(self.response[0],200)
  chat.handle_post(self.app,'/api/rides/R/chat',data,self.db);self.assertEqual(self.c.execute('SELECT COUNT(*) FROM journey_chat').fetchone()[0],1)
  chat.handle_post(self.app,'/api/rides/R/chat',{**data,'message':'Different'},self.db);self.assertEqual(self.response[0],409)
  self.user={'role':'driver','driver_id':'OTHER'};self.app.path='/api/rides/R/chat';chat.handle_get(self.app,'/api/rides/R/chat',self.db);self.assertEqual(self.response[0],403)
  self.user={'role':'driver','driver_id':'D'};chat.handle_get(self.app,'/api/rides/R/chat',self.db);self.assertEqual(self.response[0],200);self.assertNotIn('private-token',str(self.response));self.assertNotIn('phone',str(self.response))
  self.c.execute("UPDATE rides SET status='completed'");chat.handle_post(self.app,'/api/rides/R/chat',data,self.db);self.assertEqual(self.response[0],403)
