"""
zappa-lite: AI-powered mitmproxy addon that de-enshittifies the internet.

Instead of "cleaning" HTML, this DISTILLS content — extracting the meaningful
text/images and re-rendering in a minimal dark-mode reader format.

Uses async HTTP for LLM calls to avoid blocking mitmproxy's event loop.

Architecture:
  mitmproxy → response hook (async) → content filter → LLM distill → clean HTML

Inspired by George Hotz's "zappa" blog post (2026-04-15).
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from mitmproxy import http, ctx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"

# Default to a fast model for proxy use (under 2s latency)
# Slower models like qwen/qwen3-8b (~8s) make browsing unusable
# Override with ZAPPA_MODEL env var to experiment
DEFAULT_MODEL = os.environ.get("ZAPPA_MODEL", "google/gemini-2.0-flash-001")

MAX_CONTENT_SIZE = int(os.environ.get("ZAPPA_MAX_SIZE", "200000"))

# Every site gets distilled — no exceptions. You chose this browser profile.
PROCESSABLE_TYPES = {
    "text/html",
    "application/xhtml+xml",
}

# LLM call timeout — don't block the page for more than this
LLM_TIMEOUT = float(os.environ.get("ZAPPA_LLM_TIMEOUT", "20"))

# Max HTML content sent to LLM — truncate larger pages to save tokens and time
MAX_LLM_INPUT = int(os.environ.get("ZAPPA_MAX_INPUT", "30000"))

# Blocked ad/tracking domains (requests never even happen)
BLOCKED_JS_DOMAINS = {
    "googlesyndication.com", "googleadservices.com", "doubleclick.net",
    "googletagmanager.com", "google-analytics.com",
    "facebook.net", "fbcdn.net",
    "amazon-adsystem.com", "assoc-amazon.com",
    "taboola.com", "outbrain.com",
    "criteo.com", "criteo.net",
    "adnxs.com", "adroll.com",
    "quantserve.com", "scorecardresearch.com",
    "moatads.com", "sharethrough.com",
    "pubmatic.com", "rubiconproject.com",
    "openx.net", "indexww.com",
    "smartadserver.com", "smartclip.net",
}

# ---------------------------------------------------------------------------
# Distillation template
# ---------------------------------------------------------------------------

DISTILL_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --bg: #0d1117;
  --surface: #161b22;
  --border: #30363d;
  --text: #c9d1d9;
  --text-secondary: #8b949e;
  --accent: #58a6ff;
  --accent-hover: #79c0ff;
  --link: #58a6ff;
  --link-visited: #bc8cff;
  --heading: #f0f6fc;
  --code-bg: #1a1a2e;
  --success: #3fb950;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  font-size: 18px;
  line-height: 1.7;
  color: var(--text);
  background: var(--bg);
  max-width: 720px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}}
img {{ max-width: 100%; height: auto; border-radius: 6px; margin: 1em 0; }}
a {{ color: var(--link); text-decoration: none; }}
a:hover {{ color: var(--accent-hover); text-decoration: underline; }}
a:visited {{ color: var(--link-visited); }}
h1, h2, h3, h4, h5, h6 {{
  color: var(--heading);
  margin: 1.5em 0 0.5em;
  line-height: 1.3;
}}
h1 {{ font-size: 2em; }}
h2 {{ font-size: 1.5em; }}
h3 {{ font-size: 1.25em; }}
p {{ margin: 0.8em 0; }}
code {{ background: var(--code-bg); padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
pre {{ background: var(--code-bg); padding: 1em; border-radius: 6px; overflow-x: auto; margin: 1em 0; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ border-left: 3px solid var(--accent); padding-left: 1em; margin: 1em 0; color: var(--text-secondary); }}
ul, ol {{ margin: 0.8em 0; padding-left: 2em; }}
li {{ margin: 0.3em 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid var(--border); padding: 0.5em 0.8em; text-align: left; }}
th {{ background: var(--surface); }}
hr {{ border: none; border-top: 1px solid var(--border); margin: 2em 0; }}
.zappa-bar {{
  position: sticky; top: 0; z-index: 100;
  background: var(--surface); border-bottom: 1px solid var(--border);
  padding: 0.5rem 1rem; margin: -2rem -1.5rem 1.5rem;
  display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; color: var(--text-secondary);
}}
.zappa-bar a {{ color: var(--accent); font-size: 13px; }}
.zappa-bar .tag {{
  background: #1a3a2a; color: var(--success); padding: 2px 8px;
  border-radius: 3px; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.5px; font-weight: 600;
}}
</style>
</head>
<body>
<div class="zappa-bar">
  <span><span class="tag">distilled</span> zappa-lite</span>
  <span><a href="{url}">view original &rarr;</a></span>
</div>
<article>
{content}
</article>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Prompt system
# ---------------------------------------------------------------------------

DEFAULT_PROMPT = """You are a content distillation engine. You receive raw HTML from a website and EXTRACT ONLY the meaningful content, then return it as clean, minimal HTML.

Your job is NOT to "clean" or "improve" the existing HTML. Your job is to DISTILL — extract the actual content and discard everything else.

EXTRACTION RULES:
- Extract: article text, headlines, bylines, dates, product info, prices, specifications, images that are part of the content, code blocks, data tables
- DISCARD: all ads, sponsored content, affiliate links, tracking, analytics, popups, modals, cookie banners, newsletter signup forms, "related articles" sections, social sharing widgets, comment sections (unless they're the content), sidebar navigation, footers with links, header navigation bars, infinite scroll triggers, autoplay video players, "before you go" overlays, notification permission requests, chat widgets, anything that asks for attention rather than giving information

OUTPUT FORMAT:
- Return ONLY the content as clean, minimal HTML fragments — <h1>, <h2>, <p>, <ul>, <ol>, <li>, <blockquote>, <code>, <pre>, <img>, <table>, <a> tags only
- DO NOT return a complete HTML document — no <html>, <head>, <body> wrapper. Just the content elements.
- DO NOT return any CSS, JavaScript, or style attributes
- DO NOT return markdown. Return HTML tags, not markdown syntax.
- DO NOT add any explanation, preamble, or commentary — just the HTML content
- Preserve image tags with their src attributes — images that are part of the content are important
- Preserve links that point to meaningful content (other articles, sources) — discard links that are tracking, ads, or navigation
- If there's genuinely no meaningful content (empty page, redirect, binary data), return: <p><em>zappa-lite: no extractable content</em></p>

STYLE GUIDE:
- Use semantic HTML: <h1> for the main title, <h2> for sections, <p> for paragraphs
- Keep code blocks as <pre><code>
- Keep data in <table> if it's real data
- Images: keep <img src="..."> tags with meaningful alt text
- Links: keep href but strip tracking parameters (utm_*, fbclid, ref, etc.)"""

SITE_PROMPTS: dict[str, str] = {
    "news": """Additional extraction rules for news sites:
- Extract IN ORDER: headline (h1), byline + date, subheadline if present, then full article body
- Include content images with their captions
- If there's a paywall gate, extract whatever article text is visible
- Discard: "subscribe now" walls, "related stories", social share buttons, auto-play videos, newsletter popups, comment sections, "trending" sidebars""",

    "shopping": """Additional extraction rules for shopping/e-commerce sites:
- Extract: product name (h1), price, main product image, description, specifications table, real user reviews (first 5 only)
- Discard: "frequently bought together", "customers also bought", sponsored products, "deal ends in" countdown, fake urgency messages, wishlist prompts, email capture popups, breadcrumbs navigation, category navigation
- Keep "Add to Cart" or purchase links if they're present as simple links""",

    "social": """Additional extraction rules for social media:
- Extract: the main feed content, post text, usernames, timestamps, attached images/media
- Discard: "suggested for you", "promoted posts", "trending topics", engagement metrics ("X people liked this"), "turn on notifications" prompts, sidebar recommendations
- Present posts in simple chronological order, no infinite scroll""",

    "docs": """Additional extraction rules for documentation/developer sites:
- Extract: ALL technical content, code blocks, headings, navigation (as a simple list), search functionality
- Preserve syntax highlighting class names on code elements
- Discard: newsletter popups, "try our product" banners, chat widgets, marketing sections
- Keep internal links working""",

    "recipe": """Additional extraction rules for recipe sites:
- Extract IN ORDER: recipe title (h1), total time, servings, ingredients list (as <ul>), step-by-step instructions (as <ol>), any important notes
- Include the main recipe image
- DISCARD: the author's life story, "jump to recipe" links (just show the recipe), ads, "get the newsletter", related recipe carousels, nutrition data popups, pin-it buttons, print buttons
- Format ingredients as a simple bulleted list, steps as a numbered list""",

    "search": """Additional extraction rules for search engines:
- Extract: organic search results ONLY — the title, URL, and snippet for each result
- Format as a simple ordered list of results
- Discard: sponsored/ad results, "people also ask", knowledge panels (unless directly relevant), shopping carousels, "related searches", maps results
- Keep search box as a simple form: <form><input type="text" placeholder="Search"><button>Search</button></form>""",
}

DOMAIN_RULES = {
    "shopping": {"amazon.", "ebay.", "walmart.", "bestbuy.", "target.", "etsy.", "aliexpress.", "shopify.", "store."},
    "news": {"nytimes.", "bbc.", "cnn.", "reuters.", "theguardian.", "washingtonpost.", "wsj.", "medium.", "substack.", "techcrunch.", "arstechnica.", "wired.", "theverge.", "engadget.", "gizmodo.", "forbes.", "dailymail.", "foxnews."},
    "social": {"twitter.", "x.com", "facebook.", "instagram.", "tiktok.", "reddit.", "linkedin.", "threads.", "mastodon."},
    "docs": {"docs.", "developer.", "readme.", "wiki.", "stackoverflow.", "mdn.", "w3schools.", "github.com"},
    "recipe": {"allrecipes.", "foodnetwork.", "bettycrocker.", "kitchn.", "seriouseats.", "smittenkitchen.", "cooking."},
    "search": {"google.", "bing.", "duckduckgo.", "yahoo.", "baidu."},
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path.home() / ".zappa-lite"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "zappa.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("zappa-lite")


# ---------------------------------------------------------------------------
# Async LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """Async client for OpenRouter and Cerebras — non-blocking for mitmproxy."""

    def __init__(self):
        self.openrouter_key = OPENROUTER_API_KEY
        self.cerebras_key = CEREBRAS_API_KEY
        self.async_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self.async_client is None or self.async_client.is_closed:
            self.async_client = httpx.AsyncClient(timeout=LLM_TIMEOUT)
        return self.async_client

    def _get_provider_config(self, model: str) -> tuple[str, str, dict]:
        """Route to the right API based on model name."""
        if model.startswith("cerebras/"):
            actual_model = model.replace("cerebras/", "")
            base_url = CEREBRAS_BASE_URL
            api_key = self.cerebras_key
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
        else:
            base_url = OPENROUTER_BASE_URL
            api_key = self.openrouter_key
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/zappa-lite",
                "X-Title": "zappa-lite",
            }
            actual_model = model

        return base_url, actual_model, headers

    async def distill(self, content: str, url: str, model: str = DEFAULT_MODEL) -> str:
        """Send HTML to LLM and get back distilled content (async, non-blocking)."""
        base_url, actual_model, headers = self._get_provider_config(model)
        prompt = self._build_prompt(url)

        # Truncate large pages to save tokens and reduce latency
        # Most meaningful content is in the first 30K chars anyway
        if len(content) > MAX_LLM_INPUT:
            content = content[:MAX_LLM_INPUT] + "\n<!-- truncated -->"

        payload = {
            "model": actual_model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": content},
            ],
            "temperature": 0.05,
            "max_tokens": 8192,
        }

        try:
            client = await self._get_client()
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            distilled = data["choices"][0]["message"]["content"]
            distilled = self._strip_code_fences(distilled)

            title = self._extract_title(content, url)
            html = DISTILL_TEMPLATE.format(
                title=title,
                url=url,
                content=distilled,
            )
            return html

        except httpx.TimeoutException:
            log.warning(f"LLM timeout for {url} ({LLM_TIMEOUT}s)")
            return content  # Pass through on timeout
        except Exception as e:
            log.error(f"LLM call failed for {url}: {e}")
            return content  # Pass through on error — don't break browsing

    def _build_prompt(self, url: str) -> str:
        """Compose the full prompt: default + site-specific rules."""
        domain = url.lower()
        prompt = DEFAULT_PROMPT

        for category, patterns in DOMAIN_RULES.items():
            if any(p in domain for p in patterns):
                prompt += f"\n\n{SITE_PROMPTS[category]}"
                break

        return prompt

    def _strip_code_fences(self, text: str) -> str:
        """Remove markdown code fences that LLMs sometimes wrap output in."""
        text = re.sub(r"^```(?:html|xml)?\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
        return text.strip()

    def _extract_title(self, content: str, url: str) -> str:
        """Try to extract the page title from the original HTML."""
        match = re.search(r"<title[^>]*>(.*?)</title>", content, re.IGNORECASE | re.DOTALL)
        if match:
            title = match.group(1).strip()
            for sep in [" - ", " | ", " — ", " – "]:
                if sep in title:
                    title = title.split(sep)[0].strip()
                    break
            return title[:200]
        return urlparse(url).netloc


# ---------------------------------------------------------------------------
# Mitmproxy Addon (async hooks)
# ---------------------------------------------------------------------------

class ZappaLite:
    """mitmproxy addon that distills web content through an LLM."""

    def __init__(self):
        self.llm: Optional[LLMClient] = None
        self.stats = {"processed": 0, "skipped": 0, "errors": 0, "bytes_saved": 0}

    def _ensure_llm(self):
        if self.llm is None:
            self.llm = LLMClient()

    def load(self, loader):
        log.info("=" * 60)
        log.info("zappa-lite: AI-powered internet distillation proxy")
        log.info(f"Model: {DEFAULT_MODEL}")
        log.info(f"Max content size: {MAX_CONTENT_SIZE} bytes")
        log.info(f"LLM timeout: {LLM_TIMEOUT}s")
        log.info("=" * 60)

    def request(self, flow: http.HTTPFlow):
        """Block requests to known ad/tracking domains."""
        host = flow.request.pretty_host.lower()

        for blocked in BLOCKED_JS_DOMAINS:
            if blocked in host:
                flow.response = http.Response.make(
                    200, b"/* zappa-lite: blocked */",
                    {"Content-Type": "text/javascript"}
                )
                log.info(f"Blocked: {host}")
                return

    async def response(self, flow: http.HTTPFlow):
        """Intercept HTML responses and distill content (async — non-blocking)."""
        self._ensure_llm()
        url = flow.request.pretty_url

        log.info(f"RESPONSE: {url} [{flow.response.status_code}]")

        content_type = flow.response.headers.get("content-type", "")
        log.info(f"  type={content_type} size={len(flow.response.content)}B")

        # Only process HTML
        if not any(t in content_type.lower() for t in PROCESSABLE_TYPES):
            return

        content_length = len(flow.response.content)

        # Skip too-large or too-small
        if content_length > MAX_CONTENT_SIZE or content_length < 100:
            self.stats["skipped"] += 1
            return

        # Only process successful responses
        if flow.response.status_code not in (200, 203):
            self.stats["skipped"] += 1
            return

        # Skip non-page requests
        path = flow.request.path.lower()
        skip_extensions = {".json", ".xml", ".rss", ".atom", ".ico", ".svg", ".png", ".jpg", ".gif", ".webp", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".pdf"}
        if any(path.endswith(ext) for ext in skip_extensions):
            self.stats["skipped"] += 1
            return

        if "/api/" in path or path.startswith("/api"):
            self.stats["skipped"] += 1
            return

        # Decode content
        try:
            content = flow.response.get_text()
        except Exception:
            self.stats["skipped"] += 1
            return

        if not content or len(content.strip()) < 50:
            self.stats["skipped"] += 1
            return

        original_size = content_length
        log.info(f"DISTILLING: {url} ({content_type}, {original_size} bytes)")

        # Send through LLM (async — doesn't block other requests)
        start = time.time()
        distilled = await self.llm.distill(content, url)
        elapsed = time.time() - start

        # If distillation returned original content unchanged, it means error/timeout
        if distilled == content:
            self.stats["errors"] += 1
            log.warning(f"Distillation unchanged for {url} — LLM error or timeout ({elapsed:.1f}s)")
            return

        flow.response.text = distilled
        flow.response.headers["x-zappa-lite"] = "distilled"
        flow.response.headers["x-zappa-model"] = DEFAULT_MODEL
        flow.response.headers["x-zappa-time"] = f"{elapsed:.2f}s"

        new_size = len(flow.response.content)
        saved = original_size - new_size
        self.stats["bytes_saved"] += max(0, saved)
        self.stats["processed"] += 1

        log.info(
            f"DISTILLED: {url} — {original_size}B -> {new_size}B "
            f"(saved {saved}B, {elapsed:.2f}s)"
        )

        if self.stats["processed"] % 10 == 0 and self.stats["processed"] > 0:
            log.info(f"Stats: {self.stats}")


addons = [ZappaLite()]