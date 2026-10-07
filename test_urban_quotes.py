import io
import json
import time
import unittest
from unittest.mock import patch
import server


def route(minutes=40, toll=2000, km=46.1):
    result = {'distanceMeters': round(km*1000), 'duration': f'{minutes*60}s', 'staticDuration': '2400s'}
    if toll is not False:
        result['travelAdvisory'] = {'tollInfo': {} if toll is None else {
            'estimatedPrice': [{'currencyCode': 'XOF', 'units': str(toll)}]}}
    return result


class UrbanQuotes(unittest.TestCase):
    def test_airport_duration_and_tolls(self):
        for minutes, expected in [(40,15800),(60,16400),(90,17300)]:
            q=server.urban_fare_breakdown(route(minutes), True)
            self.assertEqual(q['total'],expected)
            self.assertEqual(sum(q[k] for k in ['base_fare','distance_fare','time_fare','toll_fare','rounding_fare','minimum_fare_adjustment']),expected)
            self.assertEqual(q['night_surcharge'],0)
        self.assertEqual(server.urban_fare_breakdown(route(40,False),True)['total'],13800)

    def test_unknown_toll_is_not_zero(self):
        self.assertIsNone(server.route_toll_fare(route(toll=None)))
        with self.assertRaises(RuntimeError): server.urban_fare_breakdown(route(toll=None),True)
        r=route();r['travelAdvisory']['tollInfo']['estimatedPrice'][0]['currencyCode']='EUR'
        self.assertIsNone(server.route_toll_fare(r))
        r=route();r['travelAdvisory']['tollInfo']['estimatedPrice'][0]['nanos']=500000000
        self.assertEqual(server.route_toll_fare(r),2001)

    def test_city_allowances_and_continuous_suburb_price(self):
        with patch.object(server,'route_distance_segments',return_value=[(.8,130),(.3,151)]):
            self.assertEqual(server.urban_fare_breakdown(route(4,False,1.1))['total'],1000)
        with patch.object(server,'route_distance_segments',return_value=[(2,130),(3,151)]):
            # 530 + .9*130 + 3*151 + (20-4)*14 = 1324 -> 1400
            self.assertEqual(server.urban_fare_breakdown(route(20,False,5))['total'],1400)

    def test_classic_minimum_and_itemized_total(self):
        # Short trips formerly quoted at 600 or 700 F must both reach 1000 F.
        for km, minutes, adjustment in [(1.1, 4, 400), (2, 6, 300), (3.5, 10, 0)]:
            with patch.object(server, 'route_distance_segments', return_value=[(km, 130)]):
                q = server.urban_fare_breakdown(route(minutes, False, km))
                self.assertGreaterEqual(q['total'], 1000)
                self.assertEqual(q['minimum_fare_adjustment'], adjustment)
                self.assertEqual(sum(q[k] for k in ['base_fare', 'distance_fare', 'time_fare',
                    'toll_fare', 'rounding_fare', 'minimum_fare_adjustment']), q['total'])
        for km in [.4, .8, 1.1, 2, 3, 5, 10]:
            with patch.object(server, 'route_distance_segments', return_value=[(km, 130)]):
                for minutes in [1, 4, 8, 20]:
                    self.assertGreaterEqual(server.urban_fare_breakdown(route(minutes, False, km))['total'], 1000)

    def test_live_request_and_safe_toll_free_fallback(self):
        origin={'address':'Pikine, Sénégal','lat':14.75,'lng':-17.4}
        arrival={'address':'AIBD, Sénégal','lat':14.6708,'lng':-17.0733}
        with patch.object(server,'MAPS_API_KEY','test'),patch.object(server,'AUTH_SECRET','test'),patch.object(server,'geocode_senegal',side_effect=[origin,arrival]),patch.object(server,'urlopen',side_effect=[io.BytesIO(json.dumps({'routes':[route(toll=None)]}).encode()),io.BytesIO(json.dumps({'routes':[route(90,False)]}).encode())]) as call:
            q,token=server.urban_quote(None,'Pikine','AIBD')
            self.assertEqual(q['exp']-q['calculated_at'],120)
            self.assertEqual(server.verify_dakar_quote(token),q)
            self.assertIn('sans péage',q['route_note'])
            payloads=[json.loads(c.args[0].data) for c in call.call_args_list]
            self.assertFalse(payloads[0]['routeModifiers']['avoidTolls'])
            self.assertTrue(payloads[1]['routeModifiers']['avoidTolls'])
            self.assertEqual(payloads[0]['routingPreference'],'TRAFFIC_AWARE_OPTIMAL')
            self.assertEqual(payloads[0]['extraComputations'],['TOLLS'])
        with patch.object(server,'MAPS_API_KEY','test'),patch.object(server,'AUTH_SECRET','test'),patch.object(server,'geocode_senegal',side_effect=[origin,arrival]),patch.object(server,'urban_route',return_value=route(toll=None)):
            with self.assertRaises(RuntimeError):server.urban_quote(None,'Pikine','AIBD')

    def test_corrupt_route_not_priced(self):
        for km in [float('nan'),0,999]:
            r=route();r['distanceMeters']=km*1000
            with self.assertRaises(ValueError):server.urban_fare_breakdown(r,True)
        with self.assertRaises(RuntimeError):server.route_distance_segments({'distanceMeters':1000,'polyline':{'encodedPolyline':'?'}})


if __name__ == '__main__': unittest.main()
