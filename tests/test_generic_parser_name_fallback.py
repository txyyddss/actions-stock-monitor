from __future__ import annotations

import unittest

from actions_stock_monitor.parsers.generic import GenericDomainParser, GenericParserConfig


class TestGenericParserNameFallback(unittest.TestCase):
    def test_generic_name_can_be_replaced_by_description_lead_code(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="example.test"))
        html = """
        <div class="product">
          <h3>2核心</h3>
          <p>GD-V-CM-Shared3T 99.00CNY 姣忔湀 CPU 2核心</p>
          <a href="/cart.php?a=add&pid=1">立即订购</a>
        </div>
        """
        products = parser.parse(html, base_url="https://example.test/")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, "GD-V-CM-Shared3T")

    def test_generic_name_can_use_code_after_prefix_text(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="example.test"))
        html = """
        <div class="product">
          <h3>16核心</h3>
          <p>【独享带宽】GD-V-MIX-Dedi 1000Mbps ¥15900.00CNY 每月 CPU 16核心</p>
          <a href="/cart.php?a=add&pid=92">立即订购</a>
        </div>
        """
        products = parser.parse(html, base_url="https://example.test/")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, "GD-V-MIX-Dedi")

    def test_buyvm_intro_page_link_is_not_treated_as_product(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="example.test"))
        html = """
        <div class="product">
          <h3>套餐与价格</h3>
          <a href="/Product/buyvm.html">立即购买</a>
        </div>
        """
        products = parser.parse(html, base_url="https://example.test/")
        self.assertEqual(products, [])

    def test_icon_only_add_link_is_treated_as_in_stock(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="example.test"))
        html = """
        <div class="product card">
          <h3>Plan A</h3>
          <p>$9.99 Monthly</p>
          <a class="btn btn-order"><span class="material-icons">shopping_cart</span></a>
          <a href="/index.php?/cart/special-offer/&action=add&id=122&cycle=a">
            <span class="material-icons">shopping_cart</span>
          </a>
        </div>
        """
        products = parser.parse(html, base_url="https://example.test/")
        self.assertEqual(len(products), 1)
        self.assertTrue(products[0].available)

    def test_default_config_placeholder_name_falls_back_to_url_slug(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="example.test"))
        html = """
        <div class="product card">
          <h3>默认配置，请下单时自行加配</h3>
          <p>$12.90 Monthly</p>
          <a href="/store/amd-vps/sgbgp-lite-2t">Order</a>
        </div>
        """
        products = parser.parse(html, base_url="https://example.test/")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, "sgbgp-lite-2t")

    def test_prefers_whmcs_product_id_name_span_over_traffic_line(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="cloud.ggvision.net"))
        html = """
        <div class="product clearfix" id="product400">
          <header>
            <span id="product400-name">JP-Std-7</span>
            <span class="qty">6 Available</span>
          </header>
          <div class="product-desc">
            <p id="product400-description">
              CPU - 4 AMD Ryzen Core<br />
              <strong>Traffic/Speed - 128TB @ 5000Mbps</strong>
            </p>
          </div>
          <footer>
            <a href="/index.php?rp=/store/jp-standard/jp-std-7" class="btn btn-order-now">Order Now</a>
          </footer>
        </div>
        """
        products = parser.parse(html, base_url="https://cloud.ggvision.net/index.php?rp=/store/jp-standard")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, "JP-Std-7")
        self.assertIsNone(products[0].location)

    def test_location_is_not_inferred_from_name_or_category(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="cloud.ggvision.net"))
        html = """
        <div class="product clearfix" id="product500">
          <header><span id="product500-name">US2-LAC-EPYC-Special</span></header>
          <a href="/index.php?rp=/store/special-offer/us2-lac-epyc-special">Order</a>
        </div>
        """
        products = parser.parse(html, base_url="https://cloud.ggvision.net/index.php?rp=/store/special-offer")
        self.assertEqual(len(products), 1)
        self.assertIsNone(products[0].location)

    def test_extracts_pipe_style_specs_without_numbered_fragments(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="app.kaze.network"))
        html = """
        <div class="product card">
          <h3>general-16c32g</h3>
          <div>40.00 USD 月繳</div>
          <ul>
            <li>核心 | 16 vCPU</li>
            <li>記憶體 | 32 GB</li>
            <li>硬盤 | 60 GB</li>
            <li>網絡 | 10 Gbps</li>
            <li>流量 | 8 TB</li>
            <li>IP 地址 | 1 個 IPv4</li>
          </ul>
          <a href="/cart.php?a=add&pid=1">立即購買</a>
        </div>
        """
        products = parser.parse(html, base_url="https://app.kaze.network/store/general")
        self.assertEqual(len(products), 1)
        specs = products[0].specs or {}
        self.assertEqual(specs.get("CPU"), "16 vCPU")
        self.assertEqual(specs.get("RAM"), "32 GB")
        self.assertEqual(specs.get("Disk"), "60 GB")
        self.assertEqual(specs.get("Port"), "10 Gbps")
        self.assertEqual(specs.get("Traffic"), "8 TB")
        self.assertEqual(specs.get("IPv4"), "1 個 IPv4")
        self.assertNotIn("1", specs)
        self.assertNotIn("2", specs)

    def test_extracts_multi_kv_specs_from_single_line(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="www.vps.soy"))
        html = """
        <div class="product card">
          <h3>深港IX-MINI</h3>
          <p>CPU：1 核 内存：512 MB 硬盘：10 GB 带宽：1 Gbps 流量：200 GB 端口：500 个</p>
          <a href="/cart?product=ix-mini">立即购买</a>
        </div>
        """
        products = parser.parse(html, base_url="https://www.vps.soy/cart")
        self.assertEqual(len(products), 1)
        specs = products[0].specs or {}
        self.assertEqual(specs.get("CPU"), "1 核")
        self.assertEqual(specs.get("RAM"), "512 MB")
        self.assertEqual(specs.get("Disk"), "10 GB")
        self.assertEqual(specs.get("Traffic"), "200 GB")

    def test_get_started_label_falls_back_to_url_product_slug(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="cloud.colocrossing.com"))
        html = """
        <div class="product card">
          <h3>GET STARTED</h3>
          <a href="/index.php?rp=/store/specials/black-friday-flash-offer-4gb-ram-vps-40tb-bw-2025-1">Order</a>
        </div>
        """
        products = parser.parse(html, base_url="https://cloud.colocrossing.com/index.php?rp=/store/specials")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].name, "black-friday-flash-offer-4gb-ram-vps-40tb-bw-2025-1")

    def test_customer_plans_group_listing_is_not_treated_as_product(self) -> None:
        parser = GenericDomainParser(GenericParserConfig(domain="cloud.tizz.yt"))
        html = """
        <div class="card product">
          <h3>萌云 企业级 VPS · 开通更快</h3>
          <a href="/customer/plans?group_id=2">立即查看</a>
        </div>
        """
        products = parser.parse(html, base_url="https://cloud.tizz.yt/")
        self.assertEqual(products, [])


if __name__ == "__main__":
    unittest.main()
