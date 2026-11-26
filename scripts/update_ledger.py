"""Automated fresh-change watcher and static ledger builder for histdiff-ledger-v02."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
from html import escape
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
CHANGES_DIR = ROOT / "changes"

USER_AGENT = "HistDiffLedgerBot/0.4 (+https://rcarloc.github.io/histdiff-ledger-v02/)"
MIN_WORDS = 80
MIN_CHANGED_LINES = 2
MIN_CHANGE_RATIO = 0.002
MAX_CHANGE_RATIO = 0.85
MAX_WATCHLIST = 50

BLOCK_MARKERS = (
    "just a moment...",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "verify you are human",
    "access denied",
)

VOLATILE_LINE_RE = re.compile(
    r"^\s*(?:last\s+updated|updated)\s+\d+\s+(?:seconds?|minutes?|hours?|days?)\s+ago\s*$",
    re.IGNORECASE,
)

SECRET_TOKEN_RE = re.compile(
    r"\b(?:pscale_(?:pw|tkn)_[A-Za-z0-9_=-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|sk_(?:live|test)_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})\b"
)

CURATED_RECORDS = [
    ("Zoom", "zoom.html", "Feb 2019 → Jul 2020", "2019-02-17 to 2020-07-01", "The plan comparison changed the listed participant allowance from 100 to 300."),
    ("Slack", "slack.html", "Jun 2023 → Dec 2023", "2023-06-30 to 2023-12-15", "The later capture lists unlimited workflows and app integrations."),
    ("Jira", "jira.html", "Jun 2023 → Dec 2023", "2023-06-02 to 2023-12-25", "The plan table adds Atlassian Intelligence to Premium and Enterprise."),
    ("Figma", "figma.html", "May 2025 → Sep 2025", "2025-05-01 to 2025-09-29", "The feature list adds AI design tools and advanced prototyping."),
    ("OpenAI API", "openai-api.html", "Apr 2025 → Sep 2025", "2025-04-30 to 2025-09-20", "The pricing page introduces the GPT-5 model family."),
    ("Linear", "linear.html", "Apr 2025 → Sep 2025", "2025-04-15 to 2025-09-16", "The pricing feature list adds Product Intelligence."),
    ("Stripe", "stripe.html", "May 2025 → Oct 2025", "2025-05-01 to 2025-10-01", "The later page describes accepting stablecoin payments."),
    ("Shopify", "shopify.html", "May 2025 → Sep 2025", "2025-05-01 to 2025-09-29", "The later capture lists monthly prices for annual subscriptions."),
    ("Backblaze", "backblaze.html", "Dec 2023 → Jun 2024", "2023-12-30 to 2024-06-30", "The later B2 pricing page highlights free egress up to three times monthly storage."),
    ("GitHub", "github.html", "May 2025 → Oct 2025", "2025-05-01 to 2025-10-01", "Headings for protected branches and repository rulesets disappear from the captured text."),
    ("Notion", "notion.html", "Dec 2023 → Dec 2025", "2023-12-22 to 2025-12-10", "The AI add-on FAQ gives way to a plan comparison that lists a Notion AI trial."),
    ("Discord", "discord.html", "Jan 2024 → Jan 2025", "2024-01-01 to 2025-01-01", "Discord shifted its homepage emphasis from topic-based communities toward gaming and group chat."),
]

CANDIDATE_WATCHLIST = [
    {"slug": "anthropic-pricing", "name": "Anthropic Pricing", "category": "AI Pricing", "url": "https://www.anthropic.com/pricing"},
    {"slug": "anthropic-terms", "name": "Anthropic Terms", "category": "AI Policy", "url": "https://www.anthropic.com/legal/consumer-terms"},
    {"slug": "anthropic-release-notes", "name": "Anthropic Release Notes", "category": "AI Docs", "url": "https://docs.anthropic.com/en/release-notes/overview"},
    {"slug": "openai-policies", "name": "OpenAI Usage Policies", "category": "AI Policy", "url": "https://openai.com/policies/usage-policies/"},
    {"slug": "openai-models", "name": "OpenAI Models Docs", "category": "AI Docs", "url": "https://platform.openai.com/docs/models"},
    {"slug": "openai-deprecations", "name": "OpenAI Deprecations", "category": "AI Docs", "url": "https://platform.openai.com/docs/deprecations"},
    {"slug": "google-ai-pricing", "name": "Gemini API Pricing", "category": "AI Pricing", "url": "https://ai.google.dev/gemini-api/docs/pricing"},
    {"slug": "google-ai-rate-limits", "name": "Gemini API Rate Limits", "category": "AI Docs", "url": "https://ai.google.dev/gemini-api/docs/rate-limits"},
    {"slug": "mistral-pricing", "name": "Mistral AI Pricing", "category": "AI Pricing", "url": "https://mistral.ai/pricing"},
    {"slug": "deepseek-api-pricing", "name": "DeepSeek API Pricing", "category": "AI Pricing", "url": "https://api-docs.deepseek.com/quick_start/pricing"},
    {"slug": "groq-pricing", "name": "Groq Pricing", "category": "AI Pricing", "url": "https://groq.com/pricing/"},
    {"slug": "together-pricing", "name": "Together AI Pricing", "category": "AI Pricing", "url": "https://www.together.ai/pricing"},
    {"slug": "cohere-pricing", "name": "Cohere Pricing", "category": "AI Pricing", "url": "https://cohere.com/pricing"},
    {"slug": "cursor-pricing", "name": "Cursor Pricing", "category": "AI Pricing", "url": "https://www.cursor.com/pricing"},
    {"slug": "stripe-pricing", "name": "Stripe Pricing", "category": "Dev Pricing", "url": "https://stripe.com/pricing"},
    {"slug": "stripe-api-versioning", "name": "Stripe API Versioning", "category": "Dev Docs", "url": "https://docs.stripe.com/api/versioning"},
    {"slug": "stripe-upgrades", "name": "Stripe API Upgrades", "category": "Dev Docs", "url": "https://docs.stripe.com/upgrades"},
    {"slug": "github-pricing", "name": "GitHub Pricing", "category": "Dev Pricing", "url": "https://github.com/pricing"},
    {"slug": "github-actions-limits", "name": "GitHub Actions Limits", "category": "Dev Docs", "url": "https://docs.github.com/en/actions/administering-github-actions/usage-limits-billing-and-administration"},
    {"slug": "github-copilot-plans", "name": "GitHub Copilot Plans", "category": "AI Pricing", "url": "https://docs.github.com/en/copilot/get-started/plans-for-github-copilot"},
    {"slug": "github-rest-rate-limits", "name": "GitHub REST Rate Limits", "category": "Dev Docs", "url": "https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api"},
    {"slug": "cloudflare-workers-pricing", "name": "Cloudflare Workers Pricing", "category": "Dev Pricing", "url": "https://developers.cloudflare.com/workers/platform/pricing/"},
    {"slug": "cloudflare-workers-limits", "name": "Cloudflare Workers Limits", "category": "Dev Docs", "url": "https://developers.cloudflare.com/workers/platform/limits/"},
    {"slug": "cloudflare-pages-limits", "name": "Cloudflare Pages Limits", "category": "Dev Docs", "url": "https://developers.cloudflare.com/pages/platform/limits/"},
    {"slug": "cloudflare-r2-pricing", "name": "Cloudflare R2 Pricing", "category": "Cloud Pricing", "url": "https://developers.cloudflare.com/r2/pricing/"},
    {"slug": "cloudflare-d1-pricing", "name": "Cloudflare D1 Pricing", "category": "Dev Pricing", "url": "https://developers.cloudflare.com/d1/platform/pricing/"},
    {"slug": "vercel-pricing", "name": "Vercel Pricing", "category": "Dev Pricing", "url": "https://vercel.com/pricing"},
    {"slug": "vercel-limits", "name": "Vercel Limits", "category": "Dev Docs", "url": "https://vercel.com/docs/limits"},
    {"slug": "supabase-pricing", "name": "Supabase Pricing", "category": "Dev Pricing", "url": "https://supabase.com/pricing"},
    {"slug": "linear-pricing", "name": "Linear Pricing", "category": "SaaS Pricing", "url": "https://linear.app/pricing"},
    {"slug": "figma-pricing", "name": "Figma Pricing", "category": "SaaS Pricing", "url": "https://www.figma.com/pricing/"},
    {"slug": "notion-pricing", "name": "Notion Pricing", "category": "SaaS Pricing", "url": "https://www.notion.com/pricing"},
    {"slug": "slack-pricing", "name": "Slack Pricing", "category": "SaaS Pricing", "url": "https://slack.com/pricing"},
    {"slug": "apify-pricing", "name": "Apify Pricing", "category": "Dev Pricing", "url": "https://apify.com/pricing"},
    {"slug": "backblaze-b2-pricing", "name": "Backblaze B2 Pricing", "category": "Cloud Pricing", "url": "https://www.backblaze.com/cloud-storage/pricing"},
    {"slug": "fly-pricing", "name": "Fly.io Pricing", "category": "Dev Pricing", "url": "https://fly.io/docs/about/pricing/"},
    {"slug": "render-pricing", "name": "Render Pricing", "category": "Dev Pricing", "url": "https://render.com/pricing"},
    {"slug": "railway-pricing", "name": "Railway Pricing", "category": "Dev Pricing", "url": "https://railway.com/pricing"},
    {"slug": "digitalocean-pricing", "name": "DigitalOcean Pricing", "category": "Cloud Pricing", "url": "https://www.digitalocean.com/pricing"},
    {"slug": "tailscale-pricing", "name": "Tailscale Pricing", "category": "Dev Pricing", "url": "https://tailscale.com/pricing"},
    {"slug": "clerk-pricing", "name": "Clerk Pricing", "category": "Dev Pricing", "url": "https://clerk.com/pricing"},
    {"slug": "auth0-pricing", "name": "Auth0 Pricing", "category": "Dev Pricing", "url": "https://auth0.com/pricing"},
    {"slug": "upstash-pricing", "name": "Upstash Pricing", "category": "Dev Pricing", "url": "https://upstash.com/pricing"},
    {"slug": "turso-pricing", "name": "Turso Pricing", "category": "Dev Pricing", "url": "https://turso.tech/pricing"},
    {"slug": "neon-pricing", "name": "Neon Pricing", "category": "Dev Pricing", "url": "https://neon.tech/pricing"},
    {"slug": "resend-pricing", "name": "Resend Pricing", "category": "Dev Pricing", "url": "https://resend.com/pricing"},
    {"slug": "posthog-pricing", "name": "PostHog Pricing", "category": "Dev Pricing", "url": "https://posthog.com/pricing"},
    {"slug": "sentry-pricing", "name": "Sentry Pricing", "category": "Dev Pricing", "url": "https://sentry.io/pricing/"},
    {"slug": "docker-hub-pricing", "name": "Docker Pricing", "category": "Dev Pricing", "url": "https://www.docker.com/pricing/"},
    {"slug": "docker-hub-limits", "name": "Docker Hub Usage Limits", "category": "Dev Docs", "url": "https://docs.docker.com/docker-hub/usage/"},
    {"slug": "python-docs-release", "name": "Python Status of Versions", "category": "Dev Docs", "url": "https://devguide.python.org/versions/"},
    {"slug": "kubernetes-deprecation", "name": "Kubernetes Deprecation Policy", "category": "Dev Docs", "url": "https://kubernetes.io/docs/reference/using-api/deprecation-policy/"},
    {"slug": "docker-compose-version", "name": "Docker Compose File Spec", "category": "Dev Docs", "url": "https://docs.docker.com/reference/compose-file/version-and-name/"},
    {"slug": "huggingface-hub-pricing", "name": "Hugging Face Pricing", "category": "AI Pricing", "url": "https://huggingface.co/pricing"},
]

TYPOGRAPHY_TRANS = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u00a0": " ",
})


class TextExtractor(HTMLParser):
    """DOM-aware HTML to clean line-oriented text preserving inline badges/labels."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "template"}
    BLOCK_TAGS = {
        "p", "div", "section", "article", "header", "main", "aside",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "pre", "dt", "dd",
    }
    CELL_TAGS = {"td", "th"}
    INLINE_SPACE_TAGS = {"span", "code", "mark", "small", "strong", "em", "b", "i", "a", "badge"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_main = False
        self._has_main = False
        self._tokens: list[str] = []
        self._main_tokens: list[str] = []
        self._in_tr = False

    def _buf(self) -> list[str]:
        return self._main_tokens if self._in_main else self._tokens

    def _emit_newline(self) -> None:
        buf = self._buf()
        if buf and buf[-1] != "\n":
            buf.append("\n")

    def _emit_space(self) -> None:
        buf = self._buf()
        if buf and buf[-1] not in ("\n", " ", " | "):
            buf.append(" ")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "main":
            self._has_main = True
            self._in_main = True
        if tag == "tr":
            self._in_tr = True
            self._emit_newline()
        elif tag in self.BLOCK_TAGS:
            self._emit_newline()
            if tag in ("h1", "h2", "h3"):
                self._buf().append("#" * int(tag[1]) + " ")
            elif tag == "li":
                self._buf().append("- ")
        elif tag == "br":
            self._emit_newline()
        elif tag in self.INLINE_SPACE_TAGS or tag in self.CELL_TAGS:
            self._emit_space()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self.CELL_TAGS and self._in_tr:
            buf = self._buf()
            if buf and buf[-1] != " | ":
                buf.append(" | ")
        elif tag in self.INLINE_SPACE_TAGS:
            self._emit_space()
        elif tag in self.BLOCK_TAGS:
            if tag == "tr":
                self._in_tr = False
            self._emit_newline()
        if tag == "main":
            self._in_main = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", data)
        if not cleaned.strip():
            return
        self._buf().append(cleaned)

    def get_text(self) -> str:
        raw_tokens = self._main_tokens if (self._has_main and len(" ".join(self._main_tokens).split()) >= MIN_WORDS) else (self._tokens + self._main_tokens)
        return normalize_text("".join(raw_tokens))


def normalize_text(raw: str) -> str:
    raw = SECRET_TOKEN_RE.sub("[REDACTED_TOKEN]", raw.translate(TYPOGRAPHY_TRANS))
    lines: list[str] = []
    for line in raw.splitlines():
        norm = re.sub(r"\s+", " ", line).strip(" |")
        norm = re.sub(r"\s*\|\s*", " | ", norm)
        if norm and not VOLATILE_LINE_RE.match(norm):
            lines.append(norm)
    return "\n".join(lines)


def extract_text(html_body: str) -> str:
    parser = TextExtractor()
    parser.feed(html_body)
    parser.close()
    return parser.get_text()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def word_count(text: str) -> int:
    return len(text.split())


def is_blocked_or_junk(text: str) -> bool:
    if word_count(text) < MIN_WORDS:
        return True
    lower = text.lower()
    return any(marker in lower for marker in BLOCK_MARKERS)


def evaluate_diff(old_text: str, new_text: str) -> dict | None:
    """Return diff metrics if change is meaningful and non-junk, else None."""
    old_text = normalize_text(old_text)
    new_text = normalize_text(new_text)
    if is_blocked_or_junk(new_text) or is_blocked_or_junk(old_text):
        return None
    if sha256_text(old_text) == sha256_text(new_text):
        return None
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff_lines = list(difflib.unified_diff(old_lines, new_lines, fromfile="a", tofile="b", lineterm="", n=2))
    if not diff_lines:
        return None
    changed_lines = sum(
        1 for ln in diff_lines
        if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith("+++") and not ln.startswith("---")
    )
    denom = max(len(old_lines), len(new_lines), 1)
    ratio = changed_lines / denom
    if changed_lines < MIN_CHANGED_LINES or ratio < MIN_CHANGE_RATIO or ratio > MAX_CHANGE_RATIO:
        return None
    return {
        "unified_diff": "\n".join(diff_lines),
        "changed_lines": changed_lines,
        "change_ratio": round(ratio, 4),
        "word_count_b": word_count(new_text),
    }


def summarize_diff(unified_diff: str, name: str) -> str:
    adds, dels = [], []
    for ln in unified_diff.splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            txt = ln[1:].strip()
            if len(txt) >= 12:
                adds.append(txt)
        elif ln.startswith("-") and not ln.startswith("---"):
            txt = ln[1:].strip()
            if len(txt) >= 12:
                dels.append(txt)
    if adds:
        snippet = adds[0][:115].rstrip()
        if len(adds[0]) > 115:
            snippet += "…"
        return f'Updated text includes: "{snippet}"'
    if dels:
        snippet = dels[0][:115].rstrip()
        if len(dels[0]) > 115:
            snippet += "…"
        return f'Removed text from {name}: "{snippet}"'
    return f"Detected substantive wording update on {name}."


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Validate URL is https:// and resolves only to public global IPs (SSRF guard)."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False, "Invalid URL syntax."
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        return False, "Only https:// URLs with a valid hostname are allowed."
    host = parsed.hostname.lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local") or host.endswith(".internal"):
        return False, "Local or internal hostnames are not allowed."
    try:
        ip_literal = ipaddress.ip_address(host)
        if not ip_literal.is_global:
            return False, "Non-public IP addresses are not allowed."
        return True, "OK"
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"DNS lookup failed for {host}: {exc}"
    if not infos:
        return False, f"No DNS records found for {host}."
    for info in infos:
        ip_str = info[4][0].split("%")[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"Invalid resolved address: {ip_str}"
        if not ip_obj.is_global:
            return False, f"Resolved IP {ip_str} is not globally routable."
    return True, "OK"


def fetch_url(url: str, timeout: int = 18) -> tuple[int, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    with urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return resp.status, body


def lookup_wayback_latest(url: str) -> dict | None:
    """Best-effort CDX lookup for independent archive verification."""
    try:
        q = urlencode({"url": url, "output": "json", "limit": "-1", "filter": "statuscode:200"})
        req = Request(f"https://web.archive.org/cdx/search/cdx?{q}", headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        if isinstance(rows, list) and len(rows) >= 2:
            headers = rows[0]
            row = dict(zip(headers, rows[-1]))
            ts = row.get("timestamp", "")
            orig = row.get("original", url)
            digest = row.get("digest", "")
            if ts:
                return {
                    "timestamp": ts,
                    "archive_url": f"https://web.archive.org/web/{ts}id_/{orig}",
                    "cdx_digest": digest,
                }
    except Exception:
        return None
    return None


def ping_wayback_save(url: str) -> None:
    """Best-effort Save Page Now ping so Wayback captures newly changed pages."""
    try:
        req = Request(f"https://web.archive.org/save/{url}", headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=8):
            pass
    except Exception:
        pass


def submit_indexnow(cfg: dict, changed_urls: list[str]) -> bool:
    """Ping IndexNow API (Bing / ChatGPT Search / Copilot) for newly published pages."""
    key = (cfg.get("indexnow_key") or "").strip()
    if not key or not changed_urls:
        return False
    base = cfg["base_url"].rstrip("/")
    host = urlparse(base).netloc
    payload = {
        "host": host,
        "key": key,
        "keyLocation": f"{base}/{key}.txt",
        "urlList": changed_urls,
    }
    try:
        req = Request(
            "https://api.indexnow.org/indexnow",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def analytics_tag(cfg: dict) -> str:
    tags = []
    gc = (cfg.get("goatcounter_code") or "").strip()
    if gc:
        tags.append(f'<script data-goatcounter="https://{escape(gc, quote=True)}.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>')
    cf = (cfg.get("cloudflare_beacon_token") or "").strip()
    if cf:
        payload = json.dumps({"token": cf})
        tags.append(f'<script defer src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon=\'{escape(payload, quote=True)}\'></script>')
    return "".join(tags)


def render_page_shell(
    title: str,
    description: str,
    schema: dict,
    body: str,
    cfg: dict,
    depth: int = 0,
    canonical_rel: str = "",
    og_type: str = "website",
) -> str:
    prefix = "../" * depth
    base = cfg["base_url"].rstrip("/")
    canonical_url = f"{base}/{canonical_rel.lstrip('/')}" if canonical_rel else f"{base}/"
    structured = json.dumps({"@context": "https://schema.org", **schema}, ensure_ascii=False).replace("<", r"\u003c")
    store_href = f'{cfg["store_url"]}?utm_source=histdiff_ledger&utm_medium=referral&utm_campaign=fresh_changes&utm_content=header'
    full_title = f"{title} · histdiff"
    gsc = (cfg.get("google_site_verification") or "").strip()
    gsc_meta = f'<meta name="google-site-verification" content="{escape(gsc, quote=True)}">' if gsc else ""
    meta_tags = (
        f'<link rel="canonical" href="{escape(canonical_url, quote=True)}">'
        f'{gsc_meta}'
        f'<meta property="og:title" content="{escape(full_title, quote=True)}">'
        f'<meta property="og:description" content="{escape(description, quote=True)}">'
        f'<meta property="og:type" content="{escape(og_type, quote=True)}">'
        f'<meta property="og:url" content="{escape(canonical_url, quote=True)}">'
        f'<meta name="twitter:card" content="summary">'
    )
    return (
        f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<title>{escape(full_title)}</title><meta name="description" content="{escape(description, quote=True)}">\n'
        f'{meta_tags}\n'
        f'<link rel="stylesheet" href="{prefix}style.css"><script type="application/ld+json">{structured}</script>'
        f'{analytics_tag(cfg)}</head>\n'
        f'<body><header><a class="brand" href="{prefix}index.html">hist<span>diff</span></a>'
        f'<a href="{escape(store_href, quote=True)}">Actor on Apify ↗</a></header>\n'
        f'{body}<footer>histdiff &nbsp;/&nbsp; <a href="{prefix}watchlist.html">Watchlist</a> &nbsp;/&nbsp; '
        f'<a href="{prefix}llms.txt">llms.txt</a> &nbsp;/&nbsp; <a href="{prefix}llms-full.txt">llms-full.txt</a> &nbsp;/&nbsp; '
        f'<a href="{prefix}feed.xml">Atom feed</a></footer></body></html>\n'
    )


def issue_suggest_url(cfg: dict) -> str:
    repo = cfg.get("github_repo", "rcarloc/histdiff-ledger-v02")
    params = urlencode({
        "title": "Watch request: <page name>",
        "body": "URL to monitor for changes (https://...):\nWhy wording changes on this page matter:\n",
    })
    return f"https://github.com/{repo}/issues/new?{params}"


def render_fresh_change_page(entry: dict, cfg: dict) -> str:
    name = entry["name"]
    url = entry["url"]
    summary = entry["summary"]
    date_a = entry["timestamp_a"][:10]
    date_b = entry["timestamp_b"][:10]
    actor_input = {"urls": [url], "date_start": date_a, "date_end": date_b, "tolerance_days": 3}
    deep_url = (
        f'{cfg["console_url"]}?'
        + urlencode({
            "utm_source": "histdiff_ledger",
            "utm_medium": "referral",
            "utm_campaign": "fresh_changes",
            "utm_content": "fresh_change",
            "input": json.dumps(actor_input, separators=(",", ":")),
        })
    )
    lines = []
    for line in entry["unified_diff"].splitlines():
        cls = "hunk" if line.startswith("@@") else "add" if line.startswith("+") else "del" if line.startswith("-") else "context"
        lines.append(f'<span class="{cls}">{escape(line)}</span>')

    wb_a = entry.get("wayback_a")
    wb_b = entry.get("wayback_b")
    cap_a = f'<a href="{escape(wb_a["archive_url"], quote=True)}">{escape(entry["timestamp_a"])} (Wayback ↗)</a><code>SHA256 {escape(entry["sha256_a"][:24])}… · CDX {escape(wb_a["cdx_digest"])}</code>' if wb_a else f'<span>{escape(entry["timestamp_a"])}</span><code>SHA256 {escape(entry["sha256_a"])}</code>'
    cap_b = f'<a href="{escape(wb_b["archive_url"], quote=True)}">{escape(entry["timestamp_b"])} (Wayback ↗)</a><code>SHA256 {escape(entry["sha256_b"][:24])}… · CDX {escape(wb_b["cdx_digest"])}</code>' if wb_b else f'<span>{escape(entry["timestamp_b"])}</span><code>SHA256 {escape(entry["sha256_b"])}</code>'

    body = (
        f'<main class="detail"><div class="hero"><a href="../index.html">← Change ledger</a>\n'
        f'<h1>{escape(name)}</h1><p>{escape(summary)}</p></div>'
        f'<p><a href="{escape(url, quote=True)}">{escape(url)}</a></p>\n'
        f'<div class="actions"><a class="cta" href="{escape(deep_url, quote=True)}">Compare in Apify ↗</a>'
        f'<a class="cta-secondary" href="{escape(issue_suggest_url(cfg), quote=True)}">Suggest a page to watch ↗</a></div>\n'
        f'<details><summary>Reproduce with HistDiff on Apify</summary><p>In Apify, open Input → JSON and paste this input.</p>'
        f'<pre class="actor-input">{escape(json.dumps(actor_input, indent=2))}</pre></details>\n'
        f'<div class="captures"><section><span class="meta">Earlier snapshot · UTC</span>{cap_a}</section>'
        f'<section><span class="meta">Later snapshot · UTC</span>{cap_b}</section></div>\n'
        f'<p class="stat">{entry["word_count_b"]:,} words in later snapshot · {entry["change_ratio"]:.1%} lines changed</p>\n'
        f'<details class="diff" open><summary>Full detected diff</summary><pre>{"".join(lines)}</pre></details></main>\n'
    )
    based_on = [x["archive_url"] for x in (wb_a, wb_b) if x] or [url]
    schema = {
        "@type": "Article",
        "headline": f"{name} detected page change ({date_b})",
        "description": summary,
        "about": url,
        "datePublished": entry["timestamp_b"],
        "temporalCoverage": f"{date_a}/{date_b}",
        "isBasedOn": based_on,
    }
    return render_page_shell(
        f"{name} change ({date_b})",
        summary,
        schema,
        body,
        cfg,
        depth=1,
        canonical_rel=entry["file"],
        og_type="article",
    )


def render_watchlist_page(cfg: dict, watchlist: list[dict], run_status: dict) -> str:
    now_dt = datetime.now(timezone.utc)
    date_end = now_dt.strftime("%Y-%m-%d")
    date_start = (now_dt - timedelta(days=180)).strftime("%Y-%m-%d")
    last_checked = run_status.get("last_checked_utc", "pending")[:16].replace("T", " ")
    suggest_href = escape(issue_suggest_url(cfg), quote=True)

    rows_html = []
    structured = []
    for pos, item in enumerate(watchlist, 1):
        snap = load_json(SNAPSHOTS_DIR / f'{item["slug"]}.json', {})
        words = snap.get("word_count", 0)
        sha_short = (snap.get("sha256") or "")[:12]
        fetched = (snap.get("fetched_utc") or last_checked)[:16].replace("T", " ")
        actor_input = {"urls": [item["url"]], "date_start": date_start, "date_end": date_end, "tolerance_days": 14}
        deep_url = (
            f'{cfg["console_url"]}?'
            + urlencode({
                "utm_source": "histdiff_ledger",
                "utm_medium": "referral",
                "utm_campaign": "fresh_changes",
                "utm_content": "watchlist",
                "input": json.dumps(actor_input, separators=(",", ":")),
            })
        )
        meta_txt = f'{item.get("category", "Docs")} · {words:,} words · sha256:{sha_short} · {item["url"]}'
        rows_html.append(
            f'<li><a class="record" href="{escape(deep_url, quote=True)}">'
            f'<strong>{escape(item["name"])}</strong>'
            f'<span class="summary">{escape(meta_txt)}</span>'
            f'<span class="dates">Checked {escape(fetched)}</span>'
            f'<span class="arrow" aria-hidden="true">↗</span></a></li>'
        )
        structured.append({"@type": "ListItem", "position": pos, "name": item["name"], "url": item["url"]})

    desc = f"Live directory of {len(watchlist)} developer, AI, and SaaS pricing, policy, and documentation pages monitored daily by HistDiff."
    body = (
        f'<main><div class="hero"><a href="index.html">← Change ledger</a><h1>Watchlist</h1><p>{desc}</p></div>\n'
        f'<div class="actions"><a class="cta" href="{suggest_href}">Suggest a page to watch ↗</a>'
        f'<a class="cta-secondary" href="feed.xml">Subscribe via Atom feed</a></div>\n'
        f'<div class="section-head"><h2>Monitored pages ({len(watchlist)})</h2>'
        f'<span class="section-meta">Click any row to compare last 180 days on Apify</span></div>\n'
        f'<ul class="records">{"".join(rows_html)}</ul></main>\n'
    )
    schema = {
        "@type": "CollectionPage",
        "name": "histdiff monitored watchlist",
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(watchlist), "itemListElement": structured},
    }
    return render_page_shell("Watchlist", desc, schema, body, cfg, depth=0, canonical_rel="watchlist.html")


def rebuild_site_indexes(cfg: dict, fresh_entries: list[dict], run_status: dict, watchlist: list[dict]) -> None:
    CHANGES_DIR.mkdir(parents=True, exist_ok=True)
    for entry in fresh_entries:
        rel_path = ROOT / entry["file"]
        rel_path.parent.mkdir(parents=True, exist_ok=True)
        rel_path.write_text(render_fresh_change_page(entry, cfg), encoding="utf-8")

    (ROOT / "watchlist.html").write_text(render_watchlist_page(cfg, watchlist, run_status), encoding="utf-8")

    watched_n = run_status.get("watched_count", len(watchlist))
    last_checked = run_status.get("last_checked_utc", "pending")[:16].replace("T", " ")
    suggest_href = escape(issue_suggest_url(cfg), quote=True)

    if fresh_entries:
        fresh_items_html = []
        fresh_llms = []
        fresh_full = []
        for e in fresh_entries[:30]:
            dates = f'{e["timestamp_a"][:10]} → {e["timestamp_b"][:10]}'
            fresh_items_html.append(
                f'<li><a class="record" href="{escape(e["file"], quote=True)}">'
                f'<strong>{escape(e["name"])} <span class="badge">NEW</span></strong>'
                f'<span class="summary">{escape(e["summary"])}</span>'
                f'<span class="dates">{dates}</span><span class="arrow" aria-hidden="true">↗</span></a></li>'
            )
            fresh_llms.append(f'- [{e["name"]}]({e["file"]}) — {e["timestamp_a"][:10]} to {e["timestamp_b"][:10]}. {e["summary"]}')
        for e in fresh_entries[:15]:
            diff_preview = "\n".join(e["unified_diff"].splitlines()[:80])
            fresh_full.append(
                f'### {e["name"]} ({e["timestamp_a"][:10]} to {e["timestamp_b"][:10]})\n'
                f'- URL: {e["url"]}\n- Receipt: {e["file"]}\n- Summary: {e["summary"]}\n\n```diff\n{diff_preview}\n```\n'
            )
        fresh_block = f'<ul class="records">{"".join(fresh_items_html)}</ul>'
    else:
        fresh_llms = ["- No substantive changes detected yet since watchlist initialization."]
        fresh_full = ["No substantive fresh changes published yet."]
        fresh_block = (
            f'<div class="status-box">Monitoring <strong>{watched_n}</strong> developer &amp; AI pricing/docs pages twice daily '
            f'(last check: <strong>{escape(last_checked)} UTC</strong>). No substantive wording diffs detected since baseline — '
            f'new receipt pages auto-publish here as edits occur. '
            f'<a href="watchlist.html">View all {watched_n} watched pages</a> · '
            f'<a href="{suggest_href}">Suggest a URL to watch ↗</a></div>'
        )

    curated_html = []
    structured = []
    curated_llms = []
    for pos, (name, filename, dates, iso_span, summary) in enumerate(CURATED_RECORDS, 1):
        curated_html.append(
            f'<li><a class="record" href="{filename}"><strong>{name}</strong>'
            f'<span class="summary">{summary}</span><span class="dates">{dates}</span>'
            f'<span class="arrow" aria-hidden="true">↗</span></a></li>'
        )
        structured.append({"@type": "ListItem", "position": pos, "name": name, "url": filename})
        curated_llms.append(f"- [{name}]({filename}) — {iso_span}. {summary}")

    description = "Automated change ledger for developer pricing, API limits, and archived web pages. Inspect verified diffs and original captures."
    body = (
        f'<main><div class="hero"><h1>Change ledger</h1><p>{description}</p></div>\n'
        f'<p>Featured examples: <a href="notion.html">Notion pricing</a> · <a href="figma.html">Figma pricing</a> · '
        f'<a href="watchlist.html">Live watchlist ({watched_n})</a> · '
        f'<a href="{suggest_href}">Suggest a page to watch ↗</a> · <a href="feed.xml">Atom feed</a></p>\n'
        f'<div class="section-head"><h2>Fresh detected changes</h2><span class="section-meta">Watching {watched_n} pages · Last check {escape(last_checked)} UTC</span></div>\n'
        f'{fresh_block}\n'
        f'<div class="section-head"><h2>Archived proof examples</h2><span class="section-meta">12 verified Wayback comparisons</span></div>\n'
        f'<ul class="records">{"".join(curated_html)}</ul></main>\n'
        f'<section style="max-width:960px;margin:3rem auto;padding:0 1.5rem"><h2>Use histdiff in your workflow</h2>'
        f'<p><a href="https://github.com/{cfg["github_repo"]}/releases/download/v0.2.0/histdiff-mcp-0.2.0.tgz">Download MCP wrapper v0.2.0</a> · '
        f'<a href="https://github.com/{cfg["github_repo"]}/releases/download/v0.2.0/histdiff-weekly-slack-monitor.json">Download n8n workflow</a> · '
        f'<a href="https://github.com/{cfg["github_repo"]}/releases/download/v0.2.0/n8n-setup.md">n8n setup instructions</a></p></section>\n'
    )
    index_schema = {
        "@type": "CollectionPage",
        "name": "histdiff change ledger",
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(CURATED_RECORDS), "itemListElement": structured},
    }
    (ROOT / "index.html").write_text(
        render_page_shell("Change ledger", description, index_schema, body, cfg, depth=0, canonical_rel=""),
        encoding="utf-8",
    )

    llms_text = (
        "# histdiff change ledger\n\n"
        f"> {description}\n\n"
        "## Live watchlist\n\n"
        f"- [Monitored Watchlist ({watched_n} pages)](watchlist.html) — Auto-checked twice daily.\n"
        "- [Full diff index](llms-full.txt)\n\n"
        "## Fresh detected changes\n\n"
        + "\n".join(fresh_llms)
        + "\n\n## Archived proof examples\n\n"
        + "\n".join(curated_llms)
        + "\n"
    )
    (ROOT / "llms.txt").write_text(llms_text, encoding="utf-8")

    watch_lines = [f"- {w['name']} ({w.get('category', 'Docs')}): {w['url']}" for w in watchlist]
    llms_full_text = (
        "# histdiff change ledger (full machine-readable index)\n\n"
        f"> {description}\n\n"
        "## Fresh detected changes\n\n"
        + "\n".join(fresh_full)
        + "\n\n## Archived proof examples\n\n"
        + "\n".join(curated_llms)
        + f"\n\n## Monitored URLs ({len(watchlist)})\n\n"
        + "\n".join(watch_lines)
        + "\n"
    )
    (ROOT / "llms-full.txt").write_text(llms_full_text, encoding="utf-8")

    base = cfg["base_url"].rstrip("/")
    urls = [f"{base}/", f"{base}/watchlist.html", f"{base}/llms.txt", f"{base}/llms-full.txt", f"{base}/feed.xml"]
    urls.extend(f"{base}/{e['file']}" for e in fresh_entries)
    urls.extend(f"{base}/{fname}" for _, fname, _, _, _ in CURATED_RECORDS)
    url_xml = "".join(f"  <url><loc>{escape(u)}</loc></url>\n" for u in urls)
    (ROOT / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{url_xml}</urlset>\n', encoding="utf-8")

    updated = run_status.get("last_checked_utc") or datetime.now(timezone.utc).isoformat()
    feed_entries = []
    for e in fresh_entries[:20]:
        link = f"{base}/{e['file']}"
        feed_entries.append(
            f"  <entry>\n"
            f"    <title>{escape(e['name'])} changed ({e['timestamp_b'][:10]})</title>\n"
            f"    <link href=\"{escape(link, quote=True)}\"/>\n"
            f"    <id>{escape(link)}</id>\n"
            f"    <updated>{escape(e['timestamp_b'])}</updated>\n"
            f"    <summary>{escape(e['summary'])}</summary>\n"
            f"  </entry>\n"
        )
    atom = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>histdiff Fresh Change Ledger</title>\n"
        f"  <link href=\"{escape(base + '/feed.xml', quote=True)}\" rel=\"self\"/>\n"
        f"  <link href=\"{escape(base + '/', quote=True)}\"/>\n"
        f"  <id>{escape(base + '/')}</id>\n"
        f"  <updated>{escape(updated)}</updated>\n"
        f"{''.join(feed_entries)}</feed>\n"
    )
    (ROOT / "feed.xml").write_text(atom, encoding="utf-8")
    (ROOT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")


def sync_candidates(cfg: dict, reset_existing: bool = False) -> None:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    watchlist = [] if reset_existing else load_json(DATA_DIR / "watchlist.json", [])
    existing_slugs = {item["slug"] for item in watchlist}
    existing_urls = {item["url"] for item in watchlist}

    added = 0
    for item in CANDIDATE_WATCHLIST:
        if len(watchlist) >= MAX_WATCHLIST:
            break
        if item["slug"] in existing_slugs or item["url"] in existing_urls:
            continue
        try:
            status, body = fetch_url(item["url"])
            text = extract_text(body)
            if status == 200 and not is_blocked_or_junk(text):
                snap = {
                    "slug": item["slug"],
                    "name": item["name"],
                    "url": item["url"],
                    "fetched_utc": now,
                    "sha256": sha256_text(text),
                    "word_count": word_count(text),
                    "text": text,
                }
                save_json(SNAPSHOTS_DIR / f'{item["slug"]}.json', snap)
                watchlist.append(item)
                existing_slugs.add(item["slug"])
                existing_urls.add(item["url"])
                added += 1
                print(f"ADDED {item['slug']:30s} ({snap['word_count']} words)")
            else:
                print(f"SKIP  {item['slug']:30s} (status={status}, words={word_count(text)})")
        except Exception as exc:
            print(f"FAIL  {item['slug']:30s} ({type(exc).__name__}: {exc})")

    save_json(DATA_DIR / "watchlist.json", watchlist)
    fresh = load_json(DATA_DIR / "fresh-changes.json", [])
    status_obj = load_json(DATA_DIR / "run-status.json", {})
    status_obj.update({
        "last_checked_utc": now,
        "watched_count": len(watchlist),
        "fresh_changes_count": len(fresh),
    })
    save_json(DATA_DIR / "run-status.json", status_obj)
    rebuild_site_indexes(cfg, fresh, status_obj, watchlist)
    print(f"Watchlist now has {len(watchlist)} pages (added {added}).")


def derive_slug_and_name(url: str, custom_name: str = "") -> tuple[str, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "site"
    host_short = host[4:] if host.startswith("www.") else host
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    raw_slug = "-".join([host_short.split(".")[0]] + path_parts[:2])
    slug = re.sub(r"[^a-z0-9]+", "-", raw_slug.lower()).strip("-")[:48] or "page"
    if custom_name.strip():
        return slug, custom_name.strip()[:60]
    label = " ".join([host_short] + [p.replace("-", " ").title() for p in path_parts[:2]])
    return slug, label[:60]


def add_custom_url(cfg: dict, url: str, custom_name: str = "") -> dict:
    url = url.strip()
    ok, reason = is_safe_public_url(url)
    if not ok:
        return {"added": False, "reason": reason}

    watchlist = load_json(DATA_DIR / "watchlist.json", [])
    if any(w["url"].rstrip("/") == url.rstrip("/") for w in watchlist):
        return {"added": False, "reason": "URL is already in the watchlist."}

    try:
        status, body = fetch_url(url)
    except Exception as exc:
        return {"added": False, "reason": f"Failed to fetch URL: {type(exc).__name__}: {exc}"}

    text = extract_text(body)
    if status != 200 or is_blocked_or_junk(text):
        return {"added": False, "reason": f"Page returned status={status} with {word_count(text)} clean words (minimum {MIN_WORDS}, non-blocked required)."}

    slug, name = derive_slug_and_name(url, custom_name)
    existing_slugs = {w["slug"] for w in watchlist}
    base_slug = slug
    idx = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{idx}"
        idx += 1

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    entry = {"slug": slug, "name": name, "category": "Community Request", "url": url}
    watchlist.append(entry)
    save_json(DATA_DIR / "watchlist.json", watchlist)
    save_json(SNAPSHOTS_DIR / f"{slug}.json", {
        "slug": slug, "name": name, "url": url,
        "fetched_utc": now, "sha256": sha256_text(text), "word_count": word_count(text), "text": text,
    })

    fresh = load_json(DATA_DIR / "fresh-changes.json", [])
    status_obj = load_json(DATA_DIR / "run-status.json", {})
    status_obj.update({"last_checked_utc": now, "watched_count": len(watchlist), "fresh_changes_count": len(fresh)})
    save_json(DATA_DIR / "run-status.json", status_obj)
    rebuild_site_indexes(cfg, fresh, status_obj, watchlist)
    return {"added": True, "slug": slug, "name": name, "word_count": word_count(text), "reason": "Added to watchlist and baseline snapshot captured."}


def run_daily_check(cfg: dict) -> None:
    watchlist = load_json(DATA_DIR / "watchlist.json", [])
    fresh = load_json(DATA_DIR / "fresh-changes.json", [])
    seen_keys = {(e["slug"], e["sha256_b"]) for e in fresh}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    today = now[:10]

    ok_count = 0
    failed_count = 0
    new_urls: list[str] = []

    for item in watchlist:
        slug = item["slug"]
        snap_path = SNAPSHOTS_DIR / f"{slug}.json"
        try:
            status, body = fetch_url(item["url"])
            new_text = extract_text(body)
            if status != 200 or is_blocked_or_junk(new_text):
                failed_count += 1
                print(f"SKIP {slug}: status={status}, words={word_count(new_text)}")
                continue
            ok_count += 1
            new_hash = sha256_text(new_text)
            prev = load_json(snap_path, None)
            if prev is None:
                save_json(snap_path, {
                    "slug": slug, "name": item["name"], "url": item["url"],
                    "fetched_utc": now, "sha256": new_hash, "word_count": word_count(new_text), "text": new_text,
                })
                continue
            diff = evaluate_diff(prev["text"], new_text)
            if diff is not None and (slug, new_hash) not in seen_keys:
                rel_file = f"changes/{slug}-{today}.html"
                if (ROOT / rel_file).exists():
                    rel_file = f"changes/{slug}-{today}-{new_hash[:6]}.html"
                ping_wayback_save(item["url"])
                wb = lookup_wayback_latest(item["url"])
                entry = {
                    "slug": slug,
                    "name": item["name"],
                    "category": item.get("category", "Docs/Pricing"),
                    "url": item["url"],
                    "file": rel_file,
                    "summary": summarize_diff(diff["unified_diff"], item["name"]),
                    "timestamp_a": prev["fetched_utc"],
                    "timestamp_b": now,
                    "sha256_a": prev["sha256"],
                    "sha256_b": new_hash,
                    "wayback_a": prev.get("wayback"),
                    "wayback_b": wb,
                    "word_count_b": diff["word_count_b"],
                    "change_ratio": diff["change_ratio"],
                    "unified_diff": diff["unified_diff"],
                }
                fresh.insert(0, entry)
                seen_keys.add((slug, new_hash))
                new_urls.append(f'{cfg["base_url"].rstrip("/")}/{rel_file}')
                save_json(snap_path, {
                    "slug": slug, "name": item["name"], "url": item["url"],
                    "fetched_utc": now, "sha256": new_hash, "word_count": word_count(new_text),
                    "wayback": wb, "text": new_text,
                })
                print(f"NEW  {slug} -> {rel_file} ({diff['changed_lines']} changed lines)")
            else:
                print(f"SAME {slug}")
        except (HTTPError, URLError, TimeoutError, Exception) as exc:
            failed_count += 1
            print(f"FAIL {slug}: {type(exc).__name__}: {exc}")

    save_json(DATA_DIR / "fresh-changes.json", fresh)
    status_obj = {
        "last_checked_utc": now,
        "watched_count": len(watchlist),
        "ok_count": ok_count,
        "failed_count": failed_count,
        "fresh_changes_count": len(fresh),
    }
    save_json(DATA_DIR / "run-status.json", status_obj)
    rebuild_site_indexes(cfg, fresh, status_obj, watchlist)
    if new_urls:
        pinged = submit_indexnow(cfg, new_urls + [f'{cfg["base_url"].rstrip("/")}/', f'{cfg["base_url"].rstrip("/")}/feed.xml'])
        print(f"IndexNow pinged for {len(new_urls)} new pages: {pinged}")
    print(f"Check complete: ok={ok_count}, failed={failed_count}, new_changes={len(new_urls)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-watchlist", action="store_true")
    parser.add_argument("--sync-candidates", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--add-url", type=str, default="")
    parser.add_argument("--name", type=str, default="")
    args = parser.parse_args()

    cfg = load_json(DATA_DIR / "config.json", {})
    if args.init_watchlist:
        sync_candidates(cfg, reset_existing=True)
    elif args.sync_candidates:
        sync_candidates(cfg, reset_existing=False)
    elif args.add_url:
        res = add_custom_url(cfg, args.add_url, args.name)
        print(json.dumps(res))
        return 0 if res.get("added") else 2
    elif args.render_only:
        watchlist = load_json(DATA_DIR / "watchlist.json", [])
        fresh = load_json(DATA_DIR / "fresh-changes.json", [])
        status_obj = load_json(DATA_DIR / "run-status.json", {"last_checked_utc": datetime.now(timezone.utc).isoformat(), "watched_count": len(watchlist)})
        rebuild_site_indexes(cfg, fresh, status_obj, watchlist)
    else:
        run_daily_check(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
