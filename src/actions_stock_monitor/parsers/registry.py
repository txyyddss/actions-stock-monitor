from __future__ import annotations

from .generic import GenericDomainParser, GenericParserConfig


_KNOWN_DOMAINS = [
    "fachost.cloud",
    "my.rfchost.com",
    "app.vmiss.com",
    "acck.io",
    "console.po0.com",
    "akile.io",
    "greencloudvps.com",
    "app.kaze.network",
    "bgp.gd",
    "nmcloud.cc",
    "my.frantech.ca",
    "wawo.wiki",
    "backwaves.net",
    "cloud.ggvision.net",
    "wap.ac",
    "www.bagevm.com",
]

_PARSERS = {d: GenericDomainParser(GenericParserConfig(domain=d)) for d in _KNOWN_DOMAINS}


def get_parser_for_domain(domain: str) -> GenericDomainParser:
    domain = domain.lower()
    return _PARSERS.get(domain) or GenericDomainParser(GenericParserConfig(domain=domain))
