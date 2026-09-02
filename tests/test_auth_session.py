import time
import os
from unittest.mock import patch

from carenote.auth import issue_token, verify_token
from carenote.errors import ForbiddenError
from tests.helpers import ServiceTestCase
from server import DEFAULT_SESSION_SECRET, validate_runtime_config


class TestSignedSession(ServiceTestCase):
    def test_valid_signature_resolves_actor_and_tamper_is_rejected(self):
        secret = "unit-test-secret"
        token = issue_token("clinician-lim", secret)
        self.assertEqual(verify_token(token, secret), "clinician-lim")
        with self.assertRaises(ForbiddenError):
            verify_token(token + "tampered", secret)
        with patch.dict(os.environ, {"DEMO_MODE": "0", "CARENOTE_SESSION_SECRET": DEFAULT_SESSION_SECRET}, clear=False):
            with self.assertRaises(RuntimeError):
                validate_runtime_config()
        with patch.dict(os.environ, {"DEMO_MODE": "0", "CARENOTE_SESSION_SECRET": "local-demo-secret-change-me"}, clear=False):
            with self.assertRaises(RuntimeError):
                validate_runtime_config()
        with patch.dict(os.environ, {"DEMO_MODE": "0", "CARENOTE_SESSION_SECRET": "production-test-secret"}, clear=False):
            validate_runtime_config()

    def test_expired_session_is_rejected(self):
        token = issue_token("staff-jia", "unit-test-secret", ttl_seconds=-1)
        with self.assertRaises(ForbiddenError):
            verify_token(token, "unit-test-secret")
