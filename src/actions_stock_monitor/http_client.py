from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from requests import Response
from requests.adapters import HTTPAdapter


DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int | None
    ok: bool
    text: str | None
    error: str | None
    elapsed_ms: int


@dataclass
class _CookieContext:
    cookies: dict[str, str]
    user_agent: str | None
    expires_at: float


class HttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        proxy_url: str | None = None,
        flaresolverr_url: str | None = None,
        user_agents: list[str] | None = None,
        max_retries: int = 2,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._proxy_url = proxy_url
        self._flaresolverr_url = flaresolverr_url.rstrip("/") if flaresolverr_url else None
        self._user_agents = user_agents or DEFAULT_USER_AGENTS
        self._max_retries = max_retries

        self._local = threading.local()
        self._cookie_lock = threading.Lock()
        self._cookie_cache: dict[str, _CookieContext] = {}

        # Keep this small-ish: monitor runs are frequent and we only need temporary reuse.
        self._cf_cookie_ttl_seconds = float(os.getenv("CF_COOKIE_TTL_SECONDS", "1800"))

    def _session(self) -> requests.Session:
        sess = getattr(self._local, "session", None)
        if isinstance(sess, requests.Session):
            return sess
        s = requests.Session()
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        self._local.session = s
        return s

    def fetch_text(self, url: str) -> FetchResult:
        direct = self._fetch_direct(url)
        if direct.ok or not self._flaresolverr_url:
            return direct
        if not self._is_likely_blocked(direct):
            return direct
        solved = self._fetch_via_flaresolverr(url)
        return solved

    @staticmethod
    def _should_retry_status(status_code: int) -> bool:
        # Transient / rate-limited responses where retry is commonly helpful.
        return status_code == 408 or status_code == 425 or status_code == 429 or (500 <= status_code <= 599)

    @staticmethod
    def _retry_after_seconds(resp: Response) -> float | None:
        try:
            raw = (resp.headers or {}).get("Retry-After")
        except Exception:
            raw = None
        if not raw:
            return None
        try:
            return float(raw)
        except Exception:
            return None

    @staticmethod
    def _sleep_backoff(attempt: int, *, retry_after_seconds: float | None = None) -> None:
        if retry_after_seconds is not None:
            time.sleep(min(5.0, max(0.0, retry_after_seconds)))
            return
        # Small exponential-ish backoff + jitter, capped to keep runs fast.
        base = min(2.5, 0.35 * attempt)
        time.sleep(base + random.random() * 0.15)

    def _headers(self, *, user_agent: str | None = None) -> dict[str, str]:
        ua = user_agent or random.choice(self._user_agents)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

    @staticmethod
    def _netloc(url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def _get_cookie_context(self, netloc: str) -> _CookieContext | None:
        if not netloc:
            return None
        now = time.time()
        with self._cookie_lock:
            ctx = self._cookie_cache.get(netloc)
            if not ctx:
                return None
            if ctx.expires_at <= now:
                self._cookie_cache.pop(netloc, None)
                return None
            return ctx

    def _store_cookie_context(self, netloc: str, *, cookies: dict[str, str] | None, user_agent: str | None, ttl_seconds: float | None = None) -> None:
        if not netloc or not cookies:
            return
        ttl = float(ttl_seconds if ttl_seconds is not None else self._cf_cookie_ttl_seconds)
        if ttl <= 0:
            return
        expires_at = time.time() + ttl
        with self._cookie_lock:
            self._cookie_cache[netloc] = _CookieContext(cookies=dict(cookies), user_agent=user_agent, expires_at=expires_at)

    def _proxies(self) -> dict[str, str] | None:
        if not self._proxy_url:
            return None
        return {"http": self._proxy_url, "https": self._proxy_url}

    def _fetch_direct(self, url: str) -> FetchResult:
        started = time.perf_counter()
        last_error: str | None = None
        netloc = self._netloc(url)
        ctx = self._get_cookie_context(netloc)
        cookies = ctx.cookies if ctx else None
        ua = ctx.user_agent if ctx else None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp: Response = self._session().get(
                    url,
                    headers=self._headers(user_agent=ua),
                    proxies=self._proxies(),
                    cookies=cookies,
                    timeout=(self._timeout_seconds, self._timeout_seconds),
                    allow_redirects=True,
                )
                if self._should_retry_status(resp.status_code) and attempt < self._max_retries:
                    last_error = f"HTTP {resp.status_code}"
                    self._sleep_backoff(attempt, retry_after_seconds=self._retry_after_seconds(resp))
                    continue
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                ok = 200 <= resp.status_code < 400
                blocked = self._looks_like_cloudflare_challenge(resp.status_code, resp.text or "")
                if blocked:
                    ok = False
                return FetchResult(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    ok=ok,
                    text=resp.text if (ok or blocked) else None,
                    error=None if ok else ("Blocked (Cloudflare)" if blocked else f"HTTP {resp.status_code}"),
                    elapsed_ms=elapsed_ms,
                )
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < self._max_retries:
                    self._sleep_backoff(attempt)
                    continue
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return FetchResult(
                    url=url,
                    status_code=None,
                    ok=False,
                    text=None,
                    error=last_error,
                    elapsed_ms=elapsed_ms,
                )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return FetchResult(url=url, status_code=None, ok=False, text=None, error=last_error, elapsed_ms=elapsed_ms)

    @staticmethod
    def _is_likely_blocked(res: FetchResult) -> bool:
        # Heuristics: Cloudflare/browser-challenge pages are often 403/503 and contain known markers.
        if not res.text:
            return res.status_code in (403, 503)
        return HttpClient._looks_like_cloudflare_challenge(res.status_code, res.text)

    @staticmethod
    def _looks_like_cloudflare_challenge(status_code: int | None, body: str) -> bool:
        if status_code in (403, 503):
            return True
        t = (body or "").lower()
        # Avoid false positives: many normal pages include Cloudflare analytics beacons.
        if "/cdn-cgi/" in t:
            strong = (
                "challenge-platform",
                "cf-chl",
                "__cf_chl",
                "jschl",
                "turnstile",
                "cf-turnstile",
            )
            if any(m in t for m in strong):
                return True
        if "just a moment" in t and "checking your browser" in t:
            return True
        if "attention required" in t and "cloudflare" in t:
            return True
        return False

    def _fetch_via_flaresolverr(self, url: str) -> FetchResult:
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": int(self._timeout_seconds * 1000),
        }
        if self._proxy_url and self._proxy_url.startswith(("http://", "https://")):
            payload["proxy"] = {"url": self._proxy_url}

        last_error: str | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._session().post(
                    f"{self._flaresolverr_url}/v1",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload),
                    timeout=(self._timeout_seconds, self._timeout_seconds),
                )
                if self._should_retry_status(resp.status_code) and attempt < self._max_retries:
                    last_error = f"HTTP {resp.status_code}"
                    self._sleep_backoff(attempt, retry_after_seconds=self._retry_after_seconds(resp))
                    continue
                if resp.status_code >= 400:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    return FetchResult(url=url, status_code=resp.status_code, ok=False, text=None, error=f"HTTP {resp.status_code}", elapsed_ms=elapsed_ms)

                data = resp.json()
                solution = data.get("solution") if isinstance(data, dict) else None
                if not isinstance(solution, dict):
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    return FetchResult(url=url, status_code=None, ok=False, text=None, error="FlareSolverr: missing solution", elapsed_ms=elapsed_ms)

                status_code = solution.get("status")
                final_url = solution.get("url") or url
                if not isinstance(final_url, str) or not final_url.startswith(("http://", "https://")):
                    final_url = url
                html = solution.get("response")
                user_agent = solution.get("userAgent") if isinstance(solution.get("userAgent"), str) else None
                cookies_raw = solution.get("cookies")
                cookies: dict[str, str] | None = None
                if isinstance(cookies_raw, list):
                    cookies = {}
                    for c in cookies_raw:
                        if not isinstance(c, dict):
                            continue
                        name = c.get("name")
                        value = c.get("value")
                        if isinstance(name, str) and isinstance(value, str) and name and value:
                            cookies[name] = value
                netloc = self._netloc(final_url) or self._netloc(url)
                if cookies:
                    self._store_cookie_context(netloc, cookies=cookies, user_agent=user_agent)

                if isinstance(status_code, int) and self._should_retry_status(status_code) and attempt < self._max_retries:
                    last_error = f"FlareSolverr failed (status={status_code})"
                    self._sleep_backoff(attempt)
                    continue

                ok = isinstance(status_code, int) and 200 <= status_code < 400 and isinstance(html, str)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return FetchResult(
                    url=str(final_url),
                    status_code=int(status_code) if isinstance(status_code, int) else None,
                    ok=ok,
                    text=html if ok else None,
                    error=None if ok else f"FlareSolverr failed (status={status_code})",
                    elapsed_ms=elapsed_ms,
                )
            except Exception as e:
                last_error = f"FlareSolverr {type(e).__name__}: {e}"
                if attempt < self._max_retries:
                    self._sleep_backoff(attempt)
                    continue
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return FetchResult(url=url, status_code=None, ok=False, text=None, error=last_error, elapsed_ms=elapsed_ms)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return FetchResult(url=url, status_code=None, ok=False, text=None, error=last_error, elapsed_ms=elapsed_ms)
