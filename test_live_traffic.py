import calendar
import io
import json
import time
import unittest
from unittest.mock import patch
import server


class LiveTraffic(unittest.TestCase):
    def result(self, seconds=3600):
        return {'routes':[{'distanceMeters':15300,'duration':f'{seconds}s','staticDuration':'1200s'}]}

    def test_traffic_unaware_and_unknown_fallback_rejected(self):
        for mode in ['FALLBACK_TRAFFIC_UNAWARE','FALLBACK_ROUTING_MODE_UNSPECIFIED',None]:
            result=self.result();result['fallbackInfo']={'routingMode':mode}
            with self.assertRaises(RuntimeError):server.traffic_route(result)

    def test_traffic_aware_fallback_is_explicit(self):
        result=self.result();result['fallbackInfo']={'routingMode':'FALLBACK_TRAFFIC_AWARE'}
        self.assertEqual(server.traffic_route(result)['traffic_routing'],'TRAFFIC_AWARE')

    def test_invalid_duration_or_distance_rejected(self):
        for value in ['NaNs','infs','0s','-4s','wrong',None]:
            result=self.result();result['routes'][0]['duration']=value
            with self.assertRaises(RuntimeError):server.traffic_route(result)
        for value in [float('nan'),float('inf'),-1,None]:
            result=self.result();result['routes'][0]['distanceMeters']=value
            with self.assertRaises(RuntimeError):server.traffic_route(result)
        for result in [{'routes':[]},{}, {'routes':[None]}]:
            with self.assertRaises(RuntimeError):server.traffic_route(result)

    def test_forward_and_reverse_keep_google_traffic_duration(self):
        a={'lat':14.7646085,'lng':-17.3920887};b={'lat':14.6697094,'lng':-17.4320111}
        for origin,destination,seconds in [(a,b,3600),(b,a,5400)]:
            with patch.object(server,'urlopen',return_value=io.BytesIO(json.dumps(self.result(seconds)).encode())) as call:
                route=server.urban_route(origin,destination)
            self.assertEqual(route['duration'],f'{seconds}s')
            payload=json.loads(call.call_args.args[0].data)
            self.assertEqual(payload['origin']['location']['latLng']['latitude'],origin['lat'])
            self.assertEqual(payload['destination']['location']['latLng']['latitude'],destination['lat'])
            self.assertEqual(payload['routingPreference'],'TRAFFIC_AWARE_OPTIMAL')
            self.assertEqual(payload['trafficModel'],'BEST_GUESS')
            self.assertIn('fallbackInfo',call.call_args.args[0].get_header('X-goog-fieldmask'))
            departure=calendar.timegm(time.strptime(payload['departureTime'],'%Y-%m-%dT%H:%M:%SZ'))
            self.assertLess(abs(departure-time.time()-30),3)

    def test_pickup_eta_does_not_invent_time_on_failure(self):
        result=self.result();result['fallbackInfo']={'routingMode':'FALLBACK_TRAFFIC_UNAWARE'}
        with patch.object(server,'MAPS_API_KEY','test'),patch.object(server,'urlopen',return_value=io.BytesIO(json.dumps(result).encode())):
            self.assertIsNone(server.compute_live_eta(14.7,-17.4,14.8,-17.4))
        with patch.object(server,'MAPS_API_KEY','test'),patch.object(server,'urlopen',return_value=io.BytesIO(json.dumps(self.result()).encode())) as call:
            self.assertEqual(server.compute_live_eta(14.7,-17.4,14.8,-17.4)['eta_seconds'],3600)
            self.assertEqual(json.loads(call.call_args.args[0].data)['routingPreference'],'TRAFFIC_AWARE_OPTIMAL')
