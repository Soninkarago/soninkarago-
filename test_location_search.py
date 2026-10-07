import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
import server


def place(name='Pikine, Sénégal', country='SN', lat=14.75, lng=-17.4, partial=False):
    return {'formatted_address': name, 'place_id': 'ChIJ-test-place', 'types': ['locality'],
            'partial_match': partial, 'address_components': [{'short_name': country, 'types': ['country']}],
            'geometry': {'location': {'lat': lat, 'lng': lng}}}

class LocationSearch(unittest.TestCase):
    def query(self, query, results):
        with patch.object(server, 'MAPS_API_KEY', 'test-key'), patch.object(server, 'urlopen', return_value=io.BytesIO(json.dumps({'status':'OK','results':results}).encode())) as network:
            result=server.search_senegal_places(query)
            return result, parse_qs(urlparse(network.call_args.args[0]).query)

    def test_text_and_exact_place_requests(self):
        results,params=self.query('Pikine',[place()])
        self.assertEqual(params['components'],['country:SN'])
        self.assertEqual(results[0]['address'],'Pikine, Sénégal')
        results,params=self.query('place_id:ChIJ-test-place',[place()])
        self.assertEqual(params['place_id'],['ChIJ-test-place'])
        self.assertNotIn('address',params)
        self.assertNotIn('key',results[0])

    def test_only_senegal_and_finite_coordinates(self):
        results,_=self.query('Pikine',[place(country='FR'),place(lat=float('nan')),place(lng=2),place()])
        self.assertEqual(len(results),1)

    def test_bounded_results_and_invalid_input(self):
        results,_=self.query('Pikine',[place() for _ in range(12)])
        self.assertEqual(len(results),5)
        with patch.object(server,'MAPS_API_KEY','key'),patch.object(server,'urlopen') as network:
            for q in ['a','place_id:bad!?']:
                with self.assertRaises(ValueError):server.search_senegal_places(q)
            network.assert_not_called()

    def test_google_failure_and_no_match(self):
        for status in ['ZERO_RESULTS','REQUEST_DENIED']:
            with patch.object(server,'MAPS_API_KEY','key'),patch.object(server,'urlopen',return_value=io.BytesIO(json.dumps({'status':status}).encode())):
                if status=='ZERO_RESULTS':self.assertEqual(server.search_senegal_places('Pikine'),[])
                else:
                    with self.assertRaises(RuntimeError):server.search_senegal_places('Pikine')

    def test_village_tariff_identity_preserved(self):
        r,_=self.query('place_id:ChIJ-test-place',[place(name='Moudéry, Sénégal')])
        self.assertEqual(r[0]['tariff_place'],'Moudéry')
        self.assertEqual(server.local_route_fare('local_moto',31,r[0]['tariff_place'],'Bakel'),3000)

    def test_exact_village_route_keeps_waypoint_and_price(self):
        points = [{'address':'Moudéry, Sénégal','tariff_place':'Moudéry','lat':15.05,'lng':-12.59},
                  {'address':'Bakel, Sénégal','tariff_place':'Bakel','lat':14.9,'lng':-12.46},
                  {'address':'Diawara, Sénégal','lat':15.0,'lng':-12.55}]
        with patch.object(server,'MAPS_API_KEY','key'), patch.object(server,'AUTH_SECRET','test-secret'), patch.object(server,'geocode_senegal',side_effect=points) as geo, patch.object(server,'reverse_geocode_senegal',return_value={}), patch.object(server,'urlopen',return_value=io.BytesIO(json.dumps({'routes':[{'distanceMeters':25900,'duration':'1800s'}]}).encode())) as network:
            quote,_=server.local_quote('local_taxi','place_id:ChIJ-origin','place_id:ChIJ-destination')
            self.assertEqual(quote['fare'],10000)
            self.assertEqual(geo.call_args.args[0],'Diawara')
            self.assertEqual(len(json.loads(network.call_args.args[0].data)['intermediates']),1)

if __name__=='__main__':unittest.main()
