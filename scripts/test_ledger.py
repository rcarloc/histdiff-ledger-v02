"""Unit tests for the automated ledger extractor, SSRF guard, diff gate, and site output."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import update_ledger as ul  # noqa: E402


class LedgerTests(unittest.TestCase):
    def test_extractor_preserves_inline_badges_and_normalizes_typography(self) -> None:
        pad = " ".join(["documentation"] * 90)
        html = f"""
        <html><body><main>
          <h1>Deploy Command</h1>
          <p><code>--compatibility-date</code> <span class="badge">required</span> <span>When using custom builds</span></p>
          <p><code>--branch</code> <span class="badge">optional</span> <span>Overrides \u201ctarget\u201d branch \u2014 v2</span></p>
          <p>Last updated 5 minutes ago</p>
          <p>{pad}</p>
        </main></body></html>
        """
        text = ul.extract_text(html)
        self.assertIn("--compatibility-date required When using custom builds", text)
        self.assertIn('--branch optional Overrides "target" branch - v2', text)
        self.assertNotIn("Last updated 5 minutes ago", text)

    def test_typography_only_change_produces_no_diff(self) -> None:
        pad = "\n".join([f"Paragraph line {i} with enough words to exceed minimum threshold cleanly." for i in range(30)])
        a = ul.extract_text(f"<main><p>It's a test - plan pricing.</p>{pad}</main>")
        b = ul.extract_text(f"<main><p>It\u2019s a test \u2014 plan pricing.</p>{pad}</main>")
        self.assertIsNone(ul.evaluate_diff(a, b))

    def test_ssrf_guard_blocks_unsafe_urls(self) -> None:
        for bad_url in (
            "http://example.com/pricing",
            "file:///etc/passwd",
            "https://localhost/admin",
            "https://127.0.0.1/status",
            "https://169.254.169.254/latest/meta-data/",
            "https://10.0.0.1/internal",
        ):
            ok, _ = ul.is_safe_public_url(bad_url)
            self.assertFalse(ok, f"Expected unsafe URL to be rejected: {bad_url}")

    def test_evaluate_diff_rejects_noise_and_accepts_real_change(self) -> None:
        base_lines = [f"Feature line number {i} with sufficient descriptive words for testing." for i in range(40)]
        old_text = "\n".join(base_lines)
        self.assertIsNone(ul.evaluate_diff(old_text, old_text))
        self.assertIsNone(ul.evaluate_diff(old_text, "Just a moment... Enable JavaScript and cookies to continue."))

        new_lines = list(base_lines)
        new_lines[4] = "Starter plan increased from $10 per seat to $15 per seat."
        new_lines[5] = "Rate limit updated from 1000 requests per minute to 600 requests per minute."
        diff = ul.evaluate_diff(old_text, "\n".join(new_lines))
        self.assertIsNotNone(diff)
        assert diff is not None
        self.assertGreaterEqual(diff["changed_lines"], 2)
        self.assertIn("$15 per seat", diff["unified_diff"])

    def test_generated_site_outputs_and_12_curated_records(self) -> None:
        cfg = ul.load_json(ROOT / "data" / "config.json", {})
        index_html = (ROOT / "index.html").read_text(encoding="utf-8")
        watchlist_html = (ROOT / "watchlist.html").read_text(encoding="utf-8")
        llms_txt = (ROOT / "llms.txt").read_text(encoding="utf-8")
        llms_full = (ROOT / "llms-full.txt").read_text(encoding="utf-8")
        sitemap_xml = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
        feed_xml = (ROOT / "feed.xml").read_text(encoding="utf-8")

        key = cfg.get("indexnow_key", "")
        self.assertTrue(key)
        self.assertEqual((ROOT / f"{key}.txt").read_text(encoding="utf-8").strip(), key)

        schema_match = re.search(r'<script type="application/ld\+json">(.*?)</script>', index_html, re.S)
        self.assertIsNotNone(schema_match)
        assert schema_match is not None
        schema = json.loads(schema_match.group(1))
        self.assertEqual(schema["mainEntity"]["numberOfItems"], 12)

        self.assertIn('<link rel="canonical"', index_html)
        self.assertIn('property="og:title"', index_html)
        self.assertIn('href="watchlist.html"', index_html)
        self.assertIn("Monitored pages", watchlist_html)
        self.assertIn("watchlist.html", sitemap_xml)
        self.assertIn("llms-full.txt", sitemap_xml)
        self.assertIn("Monitored URLs", llms_full)

        for _, fname, _, _, _ in ul.CURATED_RECORDS:
            self.assertIn(f'href="{fname}"', index_html)
            self.assertIn(f"]({fname})", llms_txt)
            self.assertIn(fname, sitemap_xml)
            self.assertTrue((ROOT / fname).exists())

        self.assertIn('<feed xmlns="http://www.w3.org/2005/Atom">', feed_xml)

    def test_render_fresh_change_page_structure(self) -> None:
        cfg = ul.load_json(ROOT / "data" / "config.json", {})
        entry = {
            "slug": "example-pricing",
            "name": "Example Pricing",
            "category": "Dev Pricing",
            "url": "https://example.com/pricing",
            "file": "changes/example-pricing-2026-11-25.html",
            "summary": 'Updated text includes: "Pro plan is $25/mo"',
            "timestamp_a": "2026-11-24T13:00:00+00:00",
            "timestamp_b": "2026-11-25T13:00:00+00:00",
            "sha256_a": "a" * 64,
            "sha256_b": "b" * 64,
            "wayback_a": None,
            "wayback_b": None,
            "word_count_b": 120,
            "change_ratio": 0.1,
            "unified_diff": "--- a\n+++ b\n@@ -1,2 +1,2 @@\n-Pro plan is $20/mo\n+Pro plan is $25/mo",
        }
        page_html = ul.render_fresh_change_page(entry, cfg)
        self.assertIn("Compare in Apify", page_html)
        self.assertIn("Suggest a page to watch", page_html)
        self.assertIn('"@type": "Article"', page_html)
        self.assertIn('<link rel="canonical"', page_html)
        self.assertIn("Pro plan is $25/mo", page_html)


if __name__ == "__main__":
    unittest.main()
