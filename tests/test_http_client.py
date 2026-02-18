from __future__ import annotations

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

        client._session.get = Mock(side_effect=[resp_502, resp_200])

        with patch("actions_stock_monitor.http_client.time.sleep", autospec=True) as _sleep:
            res = client.fetch_text("https://example.test/")

        self.assertTrue(res.ok)
        self.assertEqual(client._session.get.call_count, 2)
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

        client._session.get = Mock(return_value=resp_404)

        with patch("actions_stock_monitor.http_client.time.sleep", autospec=True) as _sleep:
            res = client.fetch_text("https://example.test/missing")

        self.assertFalse(res.ok)
        self.assertEqual(client._session.get.call_count, 1)
        self.assertEqual(res.status_code, 404)
        self.assertIsNone(res.text)

    def test_flaresolverr_retries_on_http_5xx(self) -> None:
        client = HttpClient(timeout_seconds=1.0, flaresolverr_url="http://127.0.0.1:8191", max_retries=3)

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

        client._session.post = Mock(side_effect=[resp_502, resp_200])

        with patch("actions_stock_monitor.http_client.time.sleep", autospec=True) as _sleep:
            res = client.fetch_text("https://example.test/")

        self.assertTrue(res.ok)
        self.assertEqual(client._session.post.call_count, 2)
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()

