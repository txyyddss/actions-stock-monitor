from __future__ import annotations

from .generic import GenericDomainParser, GenericParserConfig
from .greencloud import GreenCloudVpsConfig, GreenCloudVpsParser
from .spa_store_api import SpaStoreApiConfig, SpaStoreApiParser


_KNOWN_DOMAINS = [
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
    "cloud.colocrossing.com",
    "www.dmit.io",
    "clients.zgovps.com",
    "my.racknerd.com",
    "clientarea.gigsgigscloud.com",
    "cloud.boil.network",
    "cloud.bffyun.com",
    "bestvm.cloud",


]

_PARSERS = {d: GenericDomainParser(GenericParserConfig(domain=d)) for d in _KNOWN_DOMAINS}

# SPA storefronts with API-backed inventory.
_PARSERS["acck.io"] = SpaStoreApiParser(
    SpaStoreApiConfig(domain="acck.io", currency="CNY", shop_path="/shop/server", shop_query={"type": "bandwidth"})
)
_PARSERS["akile.io"] = SpaStoreApiParser(
    SpaStoreApiConfig(domain="akile.io", currency="CNY", shop_path="/shop/server", shop_query={"type": "bandwidth"})
)
_PARSERS["greencloudvps.com"] = GreenCloudVpsParser(GreenCloudVpsConfig(domain="greencloudvps.com"))


def get_parser_for_domain(domain: str):
    domain = domain.lower()
    return _PARSERS.get(domain) or GenericDomainParser(GenericParserConfig(domain=domain))
