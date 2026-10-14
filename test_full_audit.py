"""Régressions isolées du contrat mobile/serveur, sans service réel."""
import json, threading, time, unittest
from contextlib import contextmanager
from unittest.mock import patch
from urllib.request import Request, urlopen
import server

class Cursor:
    def __init__(self, row): self.row=row
    def fetchone(self): return self.row

class FullAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.App)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()
    def setUp(self):
        self.ride=dict(id='TEST',tracking_token='private-test',status='deposit_paid',payment_status='deposit_paid',vehicle='Minicar 14 places',driver_name='Chauffeur fictif',driver_phone='770000000',driver_lat=14.7,driver_lng=-17.4,driver_location_at=int(time.time()),client_lat=None,client_lng=None)
        outer=self
        class Conn:
            def execute(self,*args):return Cursor(outer.ride)
        @contextmanager
        def db():yield Conn()
        for p in [patch.object(server,'db',db),patch.object(server.journey_experience,'trust_fields',return_value={}),patch.object(server.App,'log_message'),patch.object(server.App,'check_rate',return_value=True)]:p.start();self.addCleanup(p.stop)
    def call(self,path,fields=None):
        request=Request(f'http://127.0.0.1:{self.http.server_port}/api'+path,data=json.dumps(fields).encode() if fields is not None else None,headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=3) as response:return json.load(response)
    def test_minicar_active_payment_stages_keep_recent_gps_and_contact(self):
        for state in ('deposit_paid','fully_paid'):
            self.ride['status']=state
            r=self.call('/rides/TEST?token=private-test')
            self.assertEqual(r['driver_lat'],14.7)
            self.assertEqual(r['driver_phone'],'770000000')
    def test_terminal_and_stale_positions_are_hidden(self):
        self.ride['status']='completed'
        r=self.call('/rides/TEST?token=private-test');self.assertIsNone(r['driver_lat']);self.assertEqual(r['driver_phone'],'')
        self.ride['status']='deposit_paid';self.ride['driver_location_at']=int(time.time())-61
        r=self.call('/rides/TEST?token=private-test');self.assertIsNone(r['driver_lat'])
    def test_successful_contact_explicitly_confirms_mail_transport(self):
        with patch.object(server,'deliver_contact') as send:
            r=self.call('/contact',{'name':'Test','email':'test@example.org','topic':'question','message':'Test fictif','website':''})
        send.assert_called_once();self.assertIs(r.get('email_delivered'),True)

import test_journey_http as journey_http
class PlateRenewal(journey_http.JourneyHTTP):
    test_full_synthetic_journey_and_lost_responses = None
    def test_lowercase_plate_remains_compatible_with_verified_profile(self):
        with self.db() as conn:
            conn.execute("UPDATE drivers SET vehicle_plate='test-123' WHERE id='D'")
        self.profile()
        body={'request_key':'L'*43,'route_code':'local_moto','quote_token':'signed-test','pickup_code_enabled':True,'journey_features':True,'client_name':'Client fictif','phone':'770000000','payment':'Espèces','terms_accepted':True,'privacy_accepted':True}
        status,ride=self.call('/api/rides',body)
        self.assertEqual(status,201)
        self.assertEqual(self.call('/api/rides/'+ride['id']+'/accept',{'pickup_code_supported':True})[0],200)
