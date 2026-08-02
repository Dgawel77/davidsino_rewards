"""
Tests for the crypto deposit-request layer.

Network is never touched: PRICE_FEED_DISABLED is set for the offline cases and
the price cache is primed directly for the conversion cases.

Runs under pytest OR plain stdlib:  python3 tests/test_payments.py
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import payments  # noqa: E402


class EnvMixin:
    """Set/restore environment around each test."""

    ENV_KEYS = ["BTC_ADDRESS", "ETH_ADDRESS", "LTC_ADDRESS", "SOL_ADDRESS",
                "USDT_ADDRESS", "USDC_ADDRESS", "VENMO_HANDLE", "CASHAPP_HANDLE",
                "PAYPAL_ME", "ZELLE_HANDLE", "PRICE_FEED_DISABLED"]

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in self.ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["PRICE_FEED_DISABLED"] = "1"
        payments._price_cache.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        payments._price_cache.clear()


BTC_ADDR = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
ETH_ADDR = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"


class TestMethodDiscovery(EnvMixin, unittest.TestCase):
    def test_no_methods_when_nothing_configured(self):
        self.assertEqual(payments.available_methods(), [])

    def test_configured_crypto_appears(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        methods = payments.available_methods()
        self.assertEqual(len(methods), 1)
        self.assertEqual(methods[0]["key"], "btc")
        self.assertEqual(methods[0]["kind"], "crypto")
        self.assertEqual(methods[0]["address"], BTC_ADDR)

    def test_unconfigured_crypto_stays_hidden(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        keys = {m["key"] for m in payments.available_methods()}
        self.assertNotIn("eth", keys)
        self.assertNotIn("sol", keys)

    def test_whitespace_only_address_is_ignored(self):
        os.environ["BTC_ADDRESS"] = "   "
        self.assertEqual(payments.available_methods(), [])

    def test_crypto_listed_before_handles(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        os.environ["VENMO_HANDLE"] = "davidsino"
        kinds = [m["kind"] for m in payments.available_methods()]
        self.assertEqual(kinds, ["crypto", "handle"])

    def test_handle_prefix_normalised(self):
        os.environ["VENMO_HANDLE"] = "@davidsino"
        os.environ["CASHAPP_HANDLE"] = "$davidsino"
        by_key = {m["key"]: m for m in payments.available_methods()}
        self.assertEqual(by_key["venmo"]["handle"], "@davidsino")
        self.assertEqual(by_key["venmo"]["link"], "https://venmo.com/u/davidsino")
        self.assertEqual(by_key["cashapp"]["handle"], "$davidsino")
        self.assertEqual(by_key["cashapp"]["link"], "https://cash.app/$davidsino")

    def test_get_method_returns_none_when_unconfigured(self):
        self.assertIsNone(payments.get_method("btc"))

    def test_get_method_finds_configured(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        self.assertEqual(payments.get_method("btc")["address"], BTC_ADDR)


class TestPriceConversion(EnvMixin, unittest.TestCase):
    def test_no_price_when_feed_disabled(self):
        self.assertIsNone(payments.usd_price("btc"))
        self.assertIsNone(payments.convert_usd("btc", 50))

    def test_conversion_uses_cached_price(self):
        payments._price_cache["bitcoin"] = (time.time(), 50_000.0)
        os.environ.pop("PRICE_FEED_DISABLED")
        self.assertEqual(payments.convert_usd("btc", 50), "0.00100000")

    def test_conversion_rounds_down_to_asset_decimals(self):
        payments._price_cache["ethereum"] = (time.time(), 3_000.0)
        os.environ.pop("PRICE_FEED_DISABLED")
        # 50/3000 = 0.01666..., ETH configured to 6 dp, truncated not rounded up
        self.assertEqual(payments.convert_usd("eth", 50), "0.016666")

    def test_expired_cache_is_not_reused_when_feed_disabled(self):
        payments._price_cache["bitcoin"] = (time.time() - payments.PRICE_TTL - 10, 50_000.0)
        self.assertIsNone(payments.usd_price("btc"))  # feed disabled -> no refresh

    def test_unknown_asset_has_no_price(self):
        self.assertIsNone(payments.usd_price("dogecoin"))
        self.assertIsNone(payments.convert_usd("dogecoin", 10))


class TestPaymentURIs(EnvMixin, unittest.TestCase):
    def test_bitcoin_uri_is_bip21_with_amount(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        uri = payments.payment_uri(payments.get_method("btc"), "0.00100000")
        self.assertTrue(uri.startswith(f"bitcoin:{BTC_ADDR}?"))
        self.assertIn("amount=0.00100000", uri)
        self.assertIn("label=Davidsino", uri)

    def test_bitcoin_uri_without_amount_still_valid(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        uri = payments.payment_uri(payments.get_method("btc"), None)
        self.assertTrue(uri.startswith(f"bitcoin:{BTC_ADDR}"))
        self.assertNotIn("amount=", uri)

    def test_ethereum_uri_uses_value_param(self):
        os.environ["ETH_ADDRESS"] = ETH_ADDR
        uri = payments.payment_uri(payments.get_method("eth"), "0.016666")
        self.assertTrue(uri.startswith(f"ethereum:{ETH_ADDR}?"))
        self.assertIn("value=0.016666", uri)

    def test_token_asset_falls_back_to_bare_address(self):
        os.environ["USDT_ADDRESS"] = "TXYZ1234567890"
        uri = payments.payment_uri(payments.get_method("usdt"), "50.00")
        self.assertEqual(uri, "TXYZ1234567890")

    def test_handle_uri_is_the_profile_link(self):
        os.environ["CASHAPP_HANDLE"] = "davidsino"
        uri = payments.payment_uri(payments.get_method("cashapp"))
        self.assertEqual(uri, "https://cash.app/$davidsino")


class TestInstructions(EnvMixin, unittest.TestCase):
    def test_crypto_instructions_without_price_tell_player_usd_worth(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        info = payments.build_instructions(payments.get_method("btc"), 50)
        self.assertEqual(info["kind"], "crypto")
        self.assertEqual(info["address"], BTC_ADDR)
        self.assertEqual(info["usd_amount"], 50.0)
        self.assertIsNone(info["crypto_amount"])
        self.assertIn("worth", info["note"])

    def test_crypto_instructions_with_price_give_exact_amount(self):
        os.environ["BTC_ADDRESS"] = BTC_ADDR
        os.environ.pop("PRICE_FEED_DISABLED")
        payments._price_cache["bitcoin"] = (time.time(), 50_000.0)
        info = payments.build_instructions(payments.get_method("btc"), 50)
        self.assertEqual(info["crypto_amount"], "0.00100000")
        self.assertEqual(info["price_usd"], 50000.0)
        self.assertIn("0.00100000 BTC", info["note"])
        self.assertIn("amount=0.00100000", info["uri"])

    def test_handle_instructions_carry_link_and_note(self):
        os.environ["VENMO_HANDLE"] = "davidsino"
        info = payments.build_instructions(payments.get_method("venmo"), 25)
        self.assertEqual(info["kind"], "handle")
        self.assertEqual(info["handle"], "@davidsino")
        self.assertEqual(info["usd_amount"], 25.0)
        self.assertIn("Apple Pay", info["note"])

    def test_instructions_expose_network_so_player_picks_right_chain(self):
        os.environ["USDT_ADDRESS"] = "TXYZ1234567890"
        info = payments.build_instructions(payments.get_method("usdt"), 100)
        self.assertTrue(info["network"])
        self.assertIn(info["network"], info["note"])


class TestQR(EnvMixin, unittest.TestCase):
    def test_qr_returns_none_for_empty_data(self):
        self.assertIsNone(payments.qr_png(""))

    @unittest.skipUnless(payments.QR_AVAILABLE, "qrcode library not installed")
    def test_qr_renders_png_magic_bytes(self):
        png = payments.qr_png("bitcoin:" + BTC_ADDR)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
