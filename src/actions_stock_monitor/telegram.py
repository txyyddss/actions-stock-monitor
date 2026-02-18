from __future__ import annotations

import html
import os
import sys
import time
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


def load_telegram_config() -> TelegramConfig | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return TelegramConfig(bot_token=token, chat_id=chat_id)


_LAST_SEND_AT: float | None = None


def _warn(msg: str) -> None:
    print(f"[telegram] {msg}", file=sys.stderr)


def _parse_retry_after_seconds(resp: requests.Response) -> float | None:
    hdr = resp.headers.get("Retry-After")
    if hdr:
        try:
            return float(hdr)
        except ValueError:
            pass
    try:
        payload = resp.json()
    except Exception:
        return None
    retry_after = (payload or {}).get("parameters", {}).get("retry_after")
    try:
        return float(retry_after)
    except Exception:
        return None


def send_telegram_html(*, cfg: TelegramConfig, message_html: str, timeout_seconds: float = 15.0) -> bool:
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    max_retries = int(os.getenv("TELEGRAM_MAX_RETRIES", "6"))
    base_delay_seconds = float(os.getenv("TELEGRAM_RETRY_BASE_SECONDS", "1.0"))
    min_interval_seconds = float(os.getenv("TELEGRAM_MIN_INTERVAL_SECONDS", "0.8"))

    global _LAST_SEND_AT
    for attempt in range(max_retries + 1):
        if _LAST_SEND_AT is not None:
            elapsed = time.perf_counter() - _LAST_SEND_AT
            remaining = min_interval_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)

        try:
            resp = requests.post(
                url,
                data={
                    "chat_id": cfg.chat_id,
                    "text": message_html,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                },
                timeout=(timeout_seconds, timeout_seconds),
            )
        except requests.RequestException as e:
            if attempt >= max_retries:
                _warn(f"send failed after retries: {type(e).__name__}: {e}")
                return False
            time.sleep(base_delay_seconds * (2**attempt))
            continue
        finally:
            _LAST_SEND_AT = time.perf_counter()

        if resp.status_code == 429:
            retry_after = _parse_retry_after_seconds(resp) or (base_delay_seconds * (2**attempt))
            if attempt >= max_retries:
                _warn(f"rate limited (429) after retries; retry_after={retry_after}")
                return False
            time.sleep(min(60.0, max(0.1, retry_after)))
            continue

        if 500 <= resp.status_code <= 599:
            if attempt >= max_retries:
                _warn(f"telegram server error after retries: {resp.status_code}")
                return False
            time.sleep(base_delay_seconds * (2**attempt))
            continue

        try:
            resp.raise_for_status()
        except requests.HTTPError:
            body = (resp.text or "").strip().replace("\n", " ")
            if len(body) > 300:
                body = body[:300] + "..."
            _warn(f"send failed: {resp.status_code} {body}")
            return False

        return True
    return False


def h(text: str | None) -> str:
    return html.escape(text or "", quote=True)
