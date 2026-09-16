"""Run with: python -m unittest test_dakar.py (after installing requirements)."""
import io
import json
import unittest
from unittest.mock import patch

import server


class DakarQuoteTests(unittest.TestCase):
    def test_traffic_time_affects_signed_fare(self):
        addresses = [
            {"address": "Pikine, Dakar, Sénégal", "lat": 14.75, "lng": -17.39},
            {"address": "Dakar Plateau, Dakar, Sénégal", "lat": 14.67, "lng": -17.43},
        ]
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        def routing(request, timeout):
            payload = json.loads(request.data)
            self.assertEqual(payload["routingPreference"], "TRAFFIC_AWARE")
            return Response(b'{"routes":[{"distanceMeters":18000,"duration":"2700s"}]}')
        with patch.object(server, "AUTH_SECRET", "test-secret"), \
             patch.object(server, "MAPS_API_KEY", "test-key"), \
             patch.object(server, "dakar_address", side_effect=addresses), \
             patch.object(server, "urlopen", side_effect=routing):
            quote, token = server.dakar_quote("Pikine", "Dakar Plateau")
            self.assertEqual(quote["fare"], 5900)
            self.assertEqual(quote["duration_min"], 45)
            self.assertEqual(server.verify_dakar_quote(token)["lat"], 14.75)
            body, signature = token.split(".")
            with self.assertRaises(ValueError):
                server.verify_dakar_quote(body + "." + "0" * len(signature))
            with patch.object(server.time, "time", return_value=quote["exp"] + 1):
                with self.assertRaises(ValueError):
                    server.verify_dakar_quote(token)


if __name__ == "__main__":
    unittest.main()
