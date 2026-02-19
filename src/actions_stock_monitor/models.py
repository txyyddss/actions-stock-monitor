from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Product:
    id: str
    domain: str
    url: str
    name: str
    price: str | None
    description: str | None
    specs: dict[str, str] | None
    available: bool | None
    currency: str | None = None
    raw: dict[str, Any] | None = None
    variant_of: str | None = None
    option: str | None = None
    billing_cycles: list[str] | None = None


@dataclass(frozen=True)
class DomainRun:
    domain: str
    ok: bool
    error: str | None
    duration_ms: int
    products: list[Product]


@dataclass(frozen=True)
class RunSummary:
    started_at: str
    finished_at: str
    restocks: int
    new_products: int
    domains_ok: int
    domains_error: int
