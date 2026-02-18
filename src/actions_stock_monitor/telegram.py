from __future__ import annotations

import html
import os
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


def send_telegram_html(*, cfg: TelegramConfig, message_html: str, timeout_seconds: float = 15.0) -> None:
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
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
    resp.raise_for_status()


def h(text: str | None) -> str:
    return html.escape(text or "", quote=True)
