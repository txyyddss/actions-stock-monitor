from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from actions_stock_monitor.http_client import HttpClient


class TestHttpClientRetries(unittest.TestCase):
    def test_retries_on_transient_5xx(self) -> None:
        client = HttpClient(timeout_seconds=1.0, max_retries=3)

        resp_502 = Mock()
        resp_502.status_code = 502
        resp_502.url = "https://example.test/"
        resp_502.text = "bad gateway"
        resp_502.headers = {}

        resp_200 = Mock()
        resp_200.status_code = 200
        resp_200.url = "https://example.test/"
        resp_200.text = "<html>ok</html>"
        resp_200.headers = {}

        client._session().get = Mock(side_effect=[resp_502, resp_200])

        with patch("actions_stock_monitor.http_client.time.sleep", autospec=True) as _sleep:
            res = client.fetch_text("https://example.test/")

        self.assertTrue(res.ok)
        self.assertEqual(client._session().get.call_count, 2)
        self.assertIsNotNone(res.text)
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.error)

    def test_does_not_retry_on_404(self) -> None:
        client = HttpClient(timeout_seconds=1.0, max_retries=3)

        resp_404 = Mock()
        resp_404.status_code = 404
        resp_404.url = "https://example.test/missing"
        resp_404.text = "not found"
        resp_404.headers = {}

        client._session().get = Mock(return_value=resp_404)

        with patch("actions_stock_monitor.http_client.time.sleep", autospec=True) as _sleep:
            res = client.fetch_text("https://example.test/missing")

        self.assertFalse(res.ok)
        self.assertEqual(client._session().get.call_count, 1)
        self.assertEqual(res.status_code, 404)
        self.assertIsNone(res.text)

    def test_flaresolverr_retries_on_http_5xx(self) -> None:
        client = HttpClient(timeout_seconds=1.0, flaresolverr_url="http://127.0.0.1:8191", max_retries=3)

        resp_403 = Mock()
        resp_403.status_code = 403
        resp_403.url = "https://example.test/"
        resp_403.text = "<html>Just a moment... cloudflare</html>"
        resp_403.headers = {}

        resp_502 = Mock()
        resp_502.status_code = 502
        resp_502.headers = {}

        resp_200 = Mock()
        resp_200.status_code = 200
        resp_200.headers = {}
        resp_200.json = Mock(
            return_value={
                "solution": {"status": 200, "url": "https://example.test/", "response": "<html>ok</html>"}
            }
        )

        client._session().get = Mock(return_value=resp_403)
        client._session().post = Mock(side_effect=[resp_502, resp_200])

        with patch("actions_stock_monitor.http_client.time.sleep", autospec=True) as _sleep:
            res = client.fetch_text("https://example.test/")

        self.assertTrue(res.ok)
        self.assertEqual(client._session().post.call_count, 2)
        self.assertEqual(res.status_code, 200)

    def test_flaresolverr_payload_is_v2_compatible(self) -> None:
        client = HttpClient(timeout_seconds=1.0, flaresolverr_url="http://127.0.0.1:8191", max_retries=1)

        resp_403 = Mock()
        resp_403.status_code = 403
        resp_403.url = "https://example.test/"
        resp_403.text = "<html>Just a moment... cloudflare</html>"
        resp_403.headers = {}

        resp_200 = Mock()
        resp_200.status_code = 200
        resp_200.headers = {}
        resp_200.json = Mock(
            return_value={
                "solution": {"status": 200, "url": "https://example.test/", "response": "<html>ok</html>"}
            }
        )

        client._session().get = Mock(return_value=resp_403)
        client._session().post = Mock(return_value=resp_200)

        res = client.fetch_text("https://example.test/")
        self.assertTrue(res.ok)

        payload = client._session().post.call_args.kwargs["json"]
        self.assertEqual(payload.get("cmd"), "request.get")
        self.assertEqual(payload.get("url"), "https://example.test/")
        self.assertNotIn("headers", payload)
        self.assertNotIn("userAgent", payload)

    def test_can_skip_flaresolverr_fallback(self) -> None:
        client = HttpClient(timeout_seconds=1.0, flaresolverr_url="http://127.0.0.1:8191", max_retries=1)

        resp_403 = Mock()
        resp_403.status_code = 403
        resp_403.url = "https://example.test/"
        resp_403.text = "<html>Just a moment... cloudflare</html>"
        resp_403.headers = {}

        client._session().get = Mock(return_value=resp_403)
        client._session().post = Mock()

        res = client.fetch_text("https://example.test/", allow_flaresolverr=False)

        self.assertFalse(res.ok)
        self.assertEqual(client._session().post.call_count, 0)

    def test_does_not_use_flaresolverr_for_plain_403_without_cloudflare_signals(self) -> None:
        client = HttpClient(timeout_seconds=1.0, flaresolverr_url="http://127.0.0.1:8191", max_retries=1)

        resp_403 = Mock()
        resp_403.status_code = 403
        resp_403.url = "https://example.test/"
        resp_403.text = "<html>Access denied</html>"
        resp_403.headers = {"server": "nginx"}

        client._session().get = Mock(return_value=resp_403)
        client._session().post = Mock()

        res = client.fetch_text("https://example.test/")

        self.assertFalse(res.ok)
        self.assertEqual(res.error, "HTTP 403")
        self.assertEqual(client._session().post.call_count, 0)


if __name__ == "__main__":
    unittest.main()
