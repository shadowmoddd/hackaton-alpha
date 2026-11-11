import hashlib
import hmac
import json
import os
import time
import unittest
from urllib.parse import urlencode

from fastapi import HTTPException

os.environ.setdefault("TELEGRAM_TOKEN", "test-token-for-unit-tests")

from app.api import telegram_user
from app.config import settings


class TelegramInitDataTests(unittest.TestCase):
    def setUp(self):
        self.old_token = settings.telegram_token
        settings.telegram_token = "test-token-for-unit-tests"

    def tearDown(self):
        settings.telegram_token = self.old_token

    def make_init_data(self, auth_date=None):
        values = {
            "auth_date": str(auth_date or int(time.time())),
            "user": json.dumps({"id": 123456, "first_name": "Test", "username": "test_user"}),
            "query_id": "AAE-test-query",
        }
        check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
        secret = hmac.new(b"WebAppData", settings.telegram_token.encode(), hashlib.sha256).digest()
        values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return urlencode(values)

    def test_valid_init_data_returns_telegram_identity(self):
        result = telegram_user(self.make_init_data())
        self.assertEqual(result["id"], 123456)
        self.assertEqual(result["username"], "test_user")

    def test_tampered_init_data_is_rejected(self):
        data = self.make_init_data().replace("Test", "Intruder")
        with self.assertRaises(HTTPException) as ctx:
            telegram_user(data)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_expired_init_data_is_rejected(self):
        data = self.make_init_data(int(time.time()) - 90000)
        with self.assertRaises(HTTPException) as ctx:
            telegram_user(data)
        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
