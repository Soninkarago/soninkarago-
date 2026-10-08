import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch, Mock
import server


class DriverSafety(unittest.TestCase):
    def driver(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        return dict(status='approved', compliance_verified=True, vehicle='Moto-taxi',
                    driving_licence_number='P', driving_licence_expiry=future,
                    insurance_policy_number='I', insurance_expiry=future,
                    vehicle_plate='V', registration_card_number='C')

    def test_expired_documents_stop_new_rides(self):
        for field in ('driving_licence_expiry', 'insurance_expiry'):
            d = self.driver()
            d[field] = (date.today() - timedelta(days=1)).isoformat()
            self.assertFalse(server.driver_can_receive_rides(d))

    def test_valid_today_and_optional_inspection(self):
        d = self.driver()
        d['insurance_expiry'] = date.today().isoformat()
        self.assertTrue(server.driver_can_receive_rides(d))
        d['technical_inspection_expiry'] = 'not-a-date'
        self.assertFalse(server.driver_can_receive_rides(d))

    def test_unreviewed_or_suspended_driver_cannot_receive_rides(self):
        for change in ({'status':'suspended'}, {'status':'pending'}, {'compliance_verified':False}):
            self.assertFalse(server.driver_can_receive_rides({**self.driver(), **change}))

    def authenticate(self, driver, role='driver', issued=100):
        app = object.__new__(server.App)
        app.headers = {'Authorization':'test'}
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = driver
        @contextmanager
        def db():
            yield conn
        with patch.object(server, 'db', db), patch.object(server, 'read_token', return_value={'role':role,'driver_id':'D','issued_at':issued}):
            return app.auth()

    def test_deleted_and_suspended_sessions_are_rejected(self):
        self.assertIsNone(self.authenticate(None))
        self.assertIsNone(self.authenticate({'status':'suspended','pin_reset_at':0}))

    def test_pin_reset_revokes_previous_session(self):
        self.assertIsNone(self.authenticate({'status':'approved','pin_reset_at':100}))
        self.assertIsNotNone(self.authenticate({'status':'approved','pin_reset_at':99}))

    def test_pending_applicant_can_still_complete_application(self):
        self.assertIsNotNone(self.authenticate({'status':'pending','pin_reset_at':0}, role='driver_application'))

if __name__ == '__main__':
    unittest.main()
