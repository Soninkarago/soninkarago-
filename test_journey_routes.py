import unittest,io,json
from unittest.mock import patch
import server
from test_urban_quotes import route
class RouteEnhancements(unittest.TestCase):
 def test_three_stops_in_signed_quote_and_full_google_route(self):
  origin={'address':'Pikine','lat':14.75,'lng':-17.4};end={'address':'Dakar','lat':14.71,'lng':-17.46};stops=[{'address':f'Arrêt {i}','lat':14.72+i*.005,'lng':-17.42} for i in range(3)]
  with patch.object(server,'MAPS_API_KEY','test'),patch.object(server,'AUTH_SECRET','test'),patch.object(server,'geocode_senegal',side_effect=[origin,end,*stops]),patch.object(server,'route_distance_segments',return_value=[(12,130)]),patch.object(server,'urlopen',return_value=io.BytesIO(json.dumps({'routes':[route(30,False,12)]}).encode())) as call:
   q,token=server.urban_quote(None,'A','B',['S1','S2','S3']);payload=json.loads(call.call_args.args[0].data)
   self.assertEqual(len(payload['intermediates']),3);self.assertEqual(q['stops'],stops);self.assertEqual(server.verify_dakar_quote(token)['stops'],stops);self.assertEqual(q['distance_km'],12)
 def test_excess_or_invalid_stops_rejected_before_route(self):
  p={'address':'Pikine','lat':14.75,'lng':-17.4}
  for stops in (['x']*4,{},[None],['']):
   with patch.object(server,'MAPS_API_KEY','test'),patch.object(server,'AUTH_SECRET','test'),patch.object(server,'geocode_senegal',return_value=p),patch.object(server,'urban_route') as call:
    with self.assertRaises(ValueError):server.urban_quote(None,'A','B',stops)
    call.assert_not_called()
 def test_dragged_pin_preserves_exact_coordinate(self):
  with patch.object(server,'reverse_geocode_senegal',return_value={'address':'Libellé voisin','lat':14.71,'lng':-17.41}):
   p=server.geocode_senegal('coordinate:14.725,-17.435');self.assertEqual((p['lat'],p['lng']),(14.725,-17.435))
   with self.assertRaises(ValueError):server.geocode_senegal('coordinate:48.85,2.35')
