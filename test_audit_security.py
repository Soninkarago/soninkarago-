import unittest
from unittest.mock import Mock
import server


class AuditSecurity(unittest.TestCase):
    def test_payment_amount_never_truncates(self):
        for value in ('500.1', '499.999', 'NaN', 'Infinity', '-1', '0', '2147483648', 'x'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.exact_xof_amount(value)
        for value in ('500', '500.00', '180000'):
            self.assertEqual(server.exact_xof_amount(value), int(float(value)))

    def test_tracking_secrets_are_not_logged(self):
        handler = object.__new__(server.App)
        handler.command = 'GET'
        handler.path = '/api/rides/SG-test?token=secret-tracking-value&phone=770000001'
        handler.log_message = Mock()
        handler.log_request(200, 10)
        logged = str(handler.log_message.call_args)
        self.assertIn('/api/rides/SG-test', logged)
        self.assertNotIn('secret-tracking-value', logged)
        self.assertNotIn('770000001', logged)

    def test_http_errors_do_not_log_request_secrets(self):
        handler = object.__new__(server.App)
        handler.log_message = Mock()
        handler.log_error('Invalid request %s', 'token=private-value')
        self.assertNotIn('private-value', str(handler.log_message.call_args))
