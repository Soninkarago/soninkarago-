import unittest,time
import test_journey_http as http_fixtures
import test_journey_experience as fixtures
import journey_service as service
import journey_experience as journey

class CourseReports(unittest.TestCase):
 def test_payment_amounts_and_private_data_excluded(self):
  ride={'id':'R','fare':190000,'deposit_amount':95000,'payment':'Wave','payment_status':'deposit_paid','tracking_token':'secret','phone':'private','driver_phone':'private','status':'deposit_paid'}
  report=service.report(ride)
  self.assertEqual(report['confirmed_amount'],95000);self.assertEqual(report['balance_due'],95000)
  self.assertEqual(report['payment_evidence'],'provider_confirmation');self.assertNotIn('secret',str(report));self.assertNotIn('phone',str(report))
  report=service.report({**ride,'payment':'Espèces','payment_status':'fully_paid'})
  self.assertEqual(report['confirmed_amount'],190000);self.assertEqual(report['balance_due'],0);self.assertEqual(report['payment_evidence'],'driver_confirmation')
  for status in ('unpaid','pending','failed'):
   report=service.report({**ride,'payment_status':status});self.assertEqual(report['confirmed_amount'],0)
 def test_cancellation_preview_never_claims_unperformed_refund(self):
  for status in ('searching','offered','payment_failed','payment_canceled'):
   self.assertTrue(service.cancellation({'status':status,'payment_status':'unpaid'})['automatic'])
  for status in ('searching','accepted','in_progress','awaiting_payment','fully_paid'):
   preview=service.cancellation({'status':status,'payment_status':'fully_paid'})
   self.assertFalse(preview['automatic']);self.assertIsNone(preview['fee']);self.assertIn('reste active',preview['message'])

class ServiceHTTP(unittest.TestCase):
 def setUp(self):
  http_fixtures.JourneyHTTP.setUp(self)
  with self.db() as conn:
   conn.execute('ALTER TABLE rides ADD COLUMN cancelled_at BIGINT')
   conn.execute('ALTER TABLE rides ADD COLUMN cancel_reason TEXT')
 stop=http_fixtures.JourneyHTTP.stop
 call=http_fixtures.JourneyHTTP.call
 def test_report_preview_support_retry_reply_and_cancel_are_scoped(self):
  payload={'request_key':'Q'*43,'route_code':'local_moto','quote_token':'signed-test','client_name':'Client fictif','phone':'770000000','payment':'Espèces','terms_accepted':True,'privacy_accepted':True}
  status,ride=self.call('/api/rides',payload);self.assertEqual(status,201)
  path='/api/rides/'+ride['id'];token=ride['tracking_token']
  self.assertEqual(self.call(path+'/report?token=wrong')[0],401)
  report=self.call(path+'/report?token='+token)[1];self.assertEqual(report['fare'],3000);self.assertNotIn(token,str(report))
  preview=self.call(path+'/cancellation-preview?token='+token)[1];self.assertTrue(preview['automatic']);self.assertEqual(preview['fee'],0)
  message={'ride_id':ride['id'],'tracking_token':token,'category':'payment','message':'Vérifier le paiement de cette course','request_id':'S'*43}
  status,ticket=self.call('/api/support',message);self.assertEqual(status,201)
  recovered=self.call('/api/support',message)[1];self.assertEqual(recovered['request_id'],ticket['request_id'])
  self.assertEqual(self.call('/api/support',{**message,'message':'Autre demande sous la même référence'})[0],409)
  self.assertEqual(self.call('/api/admin/journey-support/'+ticket['request_id'],{'status':'resolved','reply':'Paiement vérifié et réponse enregistrée.'},role='driver')[0],401)
  self.assertEqual(self.call('/api/admin/journey-support/'+ticket['request_id'],{'status':'in_progress','reply':'Nous vérifions le paiement de cette course.'},role='admin')[0],200)
  rows=self.call(path+'/support?token='+token)[1];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'in_progress');self.assertIn('vérifions',rows[0]['reply'])
  self.assertEqual(self.call(path+'/support?token=wrong')[0],401)
  self.assertEqual(self.call(path+'/cancel',{'tracking_token':token})[0],200)
  self.assertFalse(self.call(path+'/cancellation-preview?token='+token)[1]['automatic'])
  self.assertEqual(self.call(path+'/report?token='+token)[1]['status'],'cancelled')
 def test_minicar_completion_requires_boarding_code_after_full_payment(self):
  payload={'request_key':'N'*43,'route_code':'local_moto','quote_token':'signed-test','client_name':'Client fictif','phone':'770000000','payment':'Espèces','terms_accepted':True,'privacy_accepted':True}
  status,ride=self.call('/api/rides',payload);self.assertEqual(status,201)
  path='/api/rides/'+ride['id']
  with self.db() as conn:
   conn.execute("UPDATE rides SET vehicle='Minicar 14 places' WHERE id=%s",(ride['id'],))
   code=journey.ensure_code(conn,ride['id'])['code']
  status,error=self.call(path+'/accept',{'pickup_code_supported':True});self.assertEqual(status,409);self.assertIn('montée',error['error'])
  with self.db() as conn:
   conn.execute("UPDATE rides SET status='fully_paid',payment_status='fully_paid',driver_id='D' WHERE id=%s",(ride['id'],))
  self.assertEqual(self.call(path+'/complete',{})[0],409)
  self.assertEqual(self.call(path+'/start',{'code':code},driver='OTHER')[0],403)
  self.assertEqual(self.call(path+'/start',{'code':code})[0],200)
  self.assertEqual(self.call(path+'?token='+ride['tracking_token'])[1]['status'],'fully_paid')
  self.assertEqual(self.call(path+'/complete',{})[0],200)

class MinicarVerification(unittest.TestCase):
 setUp=fixtures.JourneyExperience.setUp
 def test_minicar_verifies_boarding_without_changing_payment_stages(self):
  self.c.execute("UPDATE rides SET vehicle='Minicar 14 places',status='deposit_paid'")
  with self.db() as conn:code=journey.ensure_code(conn,'R')['code'];self.assertEqual(journey.trust_fields(conn,dict(self.c.execute('SELECT * FROM rides').fetchone()))['pickup_code'],code)
  journey.handle_post(self.app,'/api/rides/R/start',{'code':'incorrect'},self.db);self.assertEqual(self.response[0],400)
  journey.handle_post(self.app,'/api/rides/R/start',{'code':code},self.db);self.assertEqual(self.response[0],200)
  self.assertEqual(self.c.execute('SELECT status FROM rides').fetchone()[0],'deposit_paid')
  with self.db() as conn:self.assertIsNone(journey.trust_fields(conn,dict(self.c.execute('SELECT * FROM rides').fetchone()))['pickup_code'])
  journey.handle_post(self.app,'/api/rides/R/start',{'code':code},self.db);self.assertTrue(self.response[1]['already_started'])
