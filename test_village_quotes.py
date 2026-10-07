"""Regression checks for village prices and signed booking quotes (no live bookings)."""
import io
import json
import time
import unittest
from unittest.mock import patch
import server


class VillageQuotes(unittest.TestCase):
    def setUp(self):
        self.secret = patch.object(server, 'AUTH_SECRET', 'test-only-secret').start()
        patch.object(server, 'MAPS_API_KEY', 'test-only-key').start()
        self.addCleanup(patch.stopall)

    def sign(self, quote):
        body = server.b64(json.dumps(quote).encode())
        return body + '.' + server.hmac.new(server.AUTH_SECRET.encode(), body.encode(), server.hashlib.sha256).hexdigest()

    def quote(self, service, pickup='Moudéry', destination='Bondy', km=17.1):
        def geocode(name):
            return {'address': name + ', Sénégal', 'lat': 15.05, 'lng': -12.59}
        with patch.object(server, 'geocode_senegal', side_effect=geocode) as geocoding, patch.object(server, 'reverse_geocode_senegal', side_effect=RuntimeError('unavailable')), patch.object(server, 'urlopen', return_value=io.BytesIO(json.dumps({'routes':[{'distanceMeters':round(km*1000),'duration':'1920s'}]}).encode())):
            quote, token = server.local_quote(service, pickup, destination)
            self.assertEqual(geocoding.call_args_list[1].args[0], server.local_place_name(destination))
            self.assertEqual(server.verify_local_quote(token, service), quote)
            return quote, token

    def test_three_services_both_directions_and_spelling(self):
        for service, fare in [('local_moto',2000),('local_taxi',2500),('local_tricycle',1500)]:
            for pickup, destination in [('Moudéry','Bondy'),('Mouderi','Bondji'),('Bondj','Moudery')]:
                with self.subTest(service=service, pickup=pickup):
                    quote, _ = self.quote(service, pickup, destination)
                    self.assertEqual(quote['fare'], fare)
                    self.assertEqual(quote['distance_km'],17.1)

    def test_other_villages_use_existing_grid(self):
        for service in server.LOCAL_SERVICE_CONFIG:
            quote, _ = self.quote(service,'Village A','Village B',km=12)
            self.assertEqual(quote['fare'],server.local_fare(service,12))

    def test_same_village_short_trip_keeps_small_price(self):
        for service, fare in [('local_moto',200),('local_taxi',1000),('local_tricycle',500)]:
            quote, _ = self.quote(service,'Moudéry','Moudéry',km=1)
            self.assertEqual(quote['fare'],fare)

    def test_long_trip_still_rejected(self):
        for service in server.LOCAL_SERVICE_CONFIG:
            with self.assertRaises(ValueError):
                self.quote(service,km=36)

    def test_quote_security_and_service_separation(self):
        _, token = self.quote('local_moto')
        for other in ['local_taxi','urban_car','invalid']:
            with self.assertRaises(ValueError):
                server.verify_local_quote(token,other)
        with self.assertRaises(ValueError):
            server.verify_local_quote(token[:-1] + ('0' if token[-1]!='0' else '1'),'local_moto')
        with self.assertRaises(ValueError):
            server.verify_local_quote(self.sign({'zone':'local','service_code':'local_moto','exp':time.time()-1}),'local_moto')
        with self.assertRaises(ValueError):
            server.verify_dakar_quote(token)
        urban={'zone':'dakar','exp':time.time()+300}
        self.assertEqual(server.verify_dakar_quote(self.sign(urban)),urban)
        with self.assertRaises(ValueError):
            server.verify_local_quote(self.sign(urban),'local_moto')

    def test_street_address_is_not_rewritten(self):
        self.assertEqual(server.local_place_name('Rue Bondy, Dakar'),'Rue Bondy, Dakar')
        self.assertEqual(server.local_place_name('  BONDiJ  '),'BONDiJ')
        self.assertEqual(server.local_place_name('Moudéry, Sénégal'),'Moudéry')


if __name__ == '__main__':
    unittest.main()
