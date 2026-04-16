# zappa-lite

> AI-powered internet distillation proxy. Your agent strips the garbage before it hits your browser.

Inspired by [George Hotz's "zappa" blog post](https://geohot.github.io/blog/jekyll/update/2026/04/15/zappa-mitmproxy.html). Instead of YOU navigating the enshittified internet, your AI proxy intercepts every page, distills just the content, and re-renders it in a clean dark-mode reader. Ads, popups, dark patterns, and attention-harvesting bullshit never reach your screen.

The Turing Test is over. Why should a human browse the ad-slathered internet when a cheap LLM can clean it up first?

## How It Works

```
Brave Browser → mitmproxy (port 8080) → zappa-lite addon
                                              ↓
                                    Blocks ad/tracking domains (never fetched)
                                              ↓
                                    Sends HTML to LLM for distillation
                                              ↓
                                    Returns clean dark-mode reader page
```

**What gets distilled:**
- **HTML pages** → LLM extracts only meaningful content (text, images, data), discards everything else (ads, popups, nav bars, sidebars, tracking, dark patterns)
- **Re-rendered** → Content is wrapped in a minimal dark-mode reader template with a "view original" link

**What gets blocked (never fetched at all):**
- 20+ ad/tracking domains: Google Ads, DoubleClick, GTM, Facebook Pixel, Taboola, Outbrain, Criteo, etc.

**Every site gets distilled** — no domain exceptions. You chose this browser profile specifically for de-enshittified browsing.

## Quick Start

### 1. Install dependencies

```bash
cd ~/projects/zappa-lite
uv sync
```

### 2. Start the proxy (terminal 1)

```bash
./run.sh
```

This starts mitmproxy on port 8080 with the zappa addon. API keys are auto-loaded from `~/.hermes/.env`.

### 3. Launch Brave (terminal 2)

```bash
./brave-zappa.sh
```

Opens Brave with a **separate profile** (`~/.zappa-lite/brave-profile/`) — your normal browsing is completely unaffected. All traffic routes through zappa-lite.

### 4. Install the CA cert (first time only)

The proxy needs to intercept HTTPS traffic. First run:

1. `./run.sh` generates the cert at `~/.mitmproxy/mitmproxy-ca-cert.pem`
2. The script auto-installs it into Brave's NSS database
3. Browse to `http://mitm.it` in the Brave-zappa window and install the certificate
4. Import into Firefox/Brave trust store if prompted

### 5. Browse the unenshittified internet

Any site you visit will be AI-distilled. Check response headers:
- `x-zappa-lite: distilled` → page was AI-distilled
- `x-zappa-model: google/gemini-2.0-flash-001` → which model
- `x-zappa-time: 2.13s` → distillation latency

## What Distilled Pages Look Like

Every page gets re-rendered in a minimal dark-mode reader:

- Dark background (`#0d1117`), clean typography, max-width 720px
- Sticky top bar: **`[distilled] zappa-lite`** | **`view original →`**
- Only the content that matters: article text, product info, recipes, search results
- No ads, no popups, no cookie walls, no sidebars, no dark patterns

## Configuration

| Env Var | Default | Description |
|-----|---------|-------------|
| `ZAPPA_MODEL` | `google/gemini-2.0-flash-001` | LLM model via OpenRouter. Override to experiment. |
| `ZAPPA_MAX_SIZE` | `200000` | Max response size to process (bytes). Larger = skipped. |
| `ZAPPA_MAX_INPUT` | `30000` | Max HTML chars sent to LLM. Truncates long pages. |
| `ZAPPA_LLM_TIMEOUT` | `20` | LLM call timeout in seconds. |
| `ZAPPA_PORT` | `8080` | Proxy listen port |
| `OPENROUTER_API_KEY` | (required) | Auto-loaded from `~/.hermes/.env` |
| `CEREBRAS_API_KEY` | (optional) | For cerebras/ models |

### Model Choice

| Model | Cost/1M tokens | Speed | Best For |
|-------|---------------|-------|----------|
| `google/gemini-2.0-flash-001` | ~$0.10 | ~2s | **Default** — fast, good extraction |
| `google/gemini-2.5-flash` | ~$0.30 | ~2s | Better quality, still fast |
| `qwen/qwen3.5-flash-02-23` | ~$0.065 | ~2s | Cheapest fast option |
| `meta-llama/llama-4-scout` | ~$0.08 | ~3s | Alternative |
| `qwen/qwen3-8b` | ~$0.05 | ~8s | Cheapest but slow |

## Site-Specific Prompts

zappa-lite applies different extraction prompts based on the site:

| Category | Sites | Extracts |
|----------|-------|----------|
| **news** | CNN, BBC, NYT, TechCrunch, The Verge, Forbes | Headline, byline, date, full article body |
| **shopping** | Amazon, eBay, Walmart | Product name, price, specs, real reviews |
| **social** | Twitter/X, Reddit, LinkedIn | Feed content, usernames, timestamps |
| **docs** | StackOverflow, MDN, GitHub READMEs | All technical content, code blocks |
| **recipe** | AllRecipes, Food Network | Ingredients, steps, cook time — no life stories |
| **search** | Google, DuckDuckGo | Organic results only, no ads |

Prompts are community-shareable like uBlock Origin filter lists. Edit `SITE_PROMPTS` in `zappa.py`.

## Architecture Decisions

| Decision | Why |
|----------|-----|
| **Distill, don't clean** | "Clean" preserves layout, ads hide in layout. Distill extracts content, discards everything else. |
| **Only HTML goes to LLM** | CSS and JS through an LLM breaks sites. CSS is dropped entirely (our template provides all styling). JS ad/tracking is blocked at the domain level. |
| **Async LLM calls** | mitmproxy runs on asyncio. Sync calls block the event loop and hang all other requests. |
| **Error = pass-through** | If the LLM fails or times out, the original page loads unchanged. Never break browsing. |
| **No domain exceptions** | You chose this browser profile for de-enshittified browsing. Every site gets distilled. |
| **Input truncation (30K chars)** | Most meaningful content is in the first 30K chars. Truncating keeps LLM calls fast and cheap. |
| **Brave separate profile** | `brave-zappa.sh` uses `~/.zappa-lite/brave-profile/` so your normal browsing is untouched. |

## Files

```
zappa.py          Main mitmproxy addon (async LLM distillation)
run.sh            Starts mitmproxy proxy server
brave-zappa.sh    Launches Brave with proxy (separate profile)
.env.example      Configuration template
README.md         This file
```

## Logs

All activity logs to `~/.zappa-lite/zappa.log`:
```
2026-04-16 07:47:33 [INFO] DISTILLING: https://news.ycombinator.com/ (text/html, 34620 bytes)
2026-04-16 07:47:36 [INFO] DISTILLED: https://news.ycombinator.com/ — 34620B -> 2830B (2.1s)
```

Test sites to try: `cnn.com`, `forbes.com`, `allrecipes.com`, `booking.com`, `medium.com`

## Philosophy

> The right way to ship this is probably a browser extension [...] It should be agentic, it shouldn't actually return the HTML, it should use tools and keep per site state. Imagine a skilled software engineer running in 100x real time cleaning up websites for you before you view them.
>
> Don't fall for AI browser crap that's marketed to you, that's just them wanting to control your attention better. You need an AI you can trust to fight back!

V1 is the proxy approach. V2 could go agentic — per-site state, tool-use, persistent cleaning. But the proxy model is the right starting point: intercepts everything, browser-agnostic, you own the AI that fights for you.

## License

MIT