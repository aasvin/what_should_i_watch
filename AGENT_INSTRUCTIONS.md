# Find Me Something To Watch — Agent Instructions & Design Guide

This document codifies the full design, architecture, technical decisions, and lessons learned
for recreating the Prime Video content scraper tool. An AI coding agent should read this
document in full before writing any code.

---

## 1. Product Overview

**Goal:** Scrape the Amazon Prime Video catalogue from the user's already-logged-in Chrome
browser on macOS and produce a structured JSON file of all available content.

**Streaming platforms in scope:**
- Amazon Prime Video (implemented)
- Netflix (future)

**Output:** A JSON file (`prime_video_content.json`) containing every title that is
**included with the user's Prime subscription** (rent/buy and channel add-ons are excluded).

---

## 2. Core Requirements

### Inputs
- The user's Chrome browser, already logged into Amazon Prime Video on macOS.
- No credentials are ever stored or captured by the tool.

### Outputs
A JSON array where each element has the following fields:

| Field               | Type            | Description                                              |
|---------------------|-----------------|----------------------------------------------------------|
| `name`              | string          | Title of the movie or show                               |
| `type`              | string          | `"Movie"` or `"TV Show"`                                 |
| `description`       | string          | Synopsis / plot summary                                  |
| `imdb_rating`       | string          | IMDb score, e.g. `"7.9"` (or `"N/A"`)                   |
| `imdb_review_count` | string          | Raw review count, e.g. `"54225"` (or `"N/A"`)           |
| `duration`          | string          | `"2 h 9 min"` for movies, `"8 episodes"` for TV shows   |
| `year`              | string          | Release year, e.g. `"2024"` (or `"N/A"`)                |
| `film_rating`       | string          | Content rating: `"R"`, `"PG-13"`, `"TV-14"`, etc.       |
| `genres`            | array of string | All genre + mood tags shown on the detail page           |

### Filtering rules
- **Include** only titles where `entitlement-message` starts with `"Included with Prime"`
  and does NOT contain the word `"subscription"` or `"channel"`.
- **Skip** all titles that require renting, buying, or a channel add-on subscription.

### CLI parameters (configurable)
```
python3 scraper.py --limit 10            # 10 movies AND 10 TV shows
python3 scraper.py --movie-limit 50      # 50 movies only
python3 scraper.py --tv-limit 100        # 100 TV shows only
python3 scraper.py                       # full scrape (no limit)
```

---

## 3. Architecture

### Why NOT Playwright / Selenium CDP

The first attempt used Playwright's `connect_over_cdp()` to attach to an existing Chrome
session. This **does not work on macOS** due to the App Sandbox preventing Chrome from
binding to the remote debugging port even when launched with `--remote-debugging-port=9222`.
Do not attempt this approach again.

### Chosen approach: AppleScript + Browser-side fetch

Two mechanisms are combined:

1. **AppleScript** (via Python `subprocess` + `osascript`) — used to:
   - Navigate Chrome to listing URLs
   - Scroll the page to load lazy content
   - Execute JavaScript in the active tab

2. **Browser-side `fetch()` API** — used to:
   - Batch-fetch all detail page HTML in parallel inside the browser
   - Parse the fetched HTML with `DOMParser`
   - Extract structured data using DOM queries

This is fast (~60–90 seconds for 500+ titles) and reliable.

### Critical AppleScript setup

Before running any JavaScript via AppleScript, the user must enable:
> **Chrome menu → View → Developer → Allow JavaScript from Apple Events**

Without this, AppleScript JS execution will throw an error.

### Clipboard as JS transport

JavaScript passed to AppleScript for execution must be injected via the **system clipboard**
to avoid escaping issues with quotes, backslashes, and special characters:

```python
def execute_js(js_code):
    subprocess.run(["pbcopy"], input=js_code.encode(), check=True)
    return run_applescript("""
    tell application "Google Chrome"
        set jsCode to the clipboard
        return execute active tab of front window javascript jsCode
    end tell
    """)
```

---

## 4. Two-Phase Scraping Design

### Phase 1 — Listing pages

**URLs:**
- Movies: `https://www.amazon.com/gp/video/movie`
- TV Shows: `https://www.amazon.com/gp/video/tv`

**Process:**
1. Navigate Chrome to each listing URL
2. Scroll to the bottom in increments (2× viewport height, 1.5 s pause) to trigger lazy loading
3. Run JavaScript to extract card data

**Key DOM selector:** `[data-testid="card"]`

Each card element exposes:
- `data-card-title` — title string (WARNING: sometimes just `"Season 3"` — see §6)
- `data-card-entity-type` — `"Movie"` or `"TV Show"`
- `a[href*="/gp/video/detail/"]` — link to the detail page (extract the ASIN path, strip query string)

**Listing JS pattern:**
```javascript
(function() {
    var results = [];
    var seen = new Set();
    var cards = Array.from(document.querySelectorAll('[data-testid="card"]'));
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var title = (card.getAttribute('data-card-title') || '').trim();
        var type  = card.getAttribute('data-card-entity-type') || 'Unknown';
        var link  = card.querySelector('a[href*="/gp/video/detail/"]');
        var url   = link ? 'https://www.amazon.com' + link.getAttribute('href').split('?')[0] : null;
        if (title && url && !seen.has(title)) {
            seen.add(title);
            results.push({ name: title, type: type, detail_url: url });
        }
    }
    return JSON.stringify(results);
})()
```

---

### Phase 2 — Detail page batch fetch

All detail URLs collected in Phase 1 are fetched **in parallel inside the browser** using
`fetch()` with `credentials: 'include'` so Amazon's session cookies are sent automatically.

**Batching:** 8 URLs per batch, 300 ms between batches (to avoid rate limiting).

**Progress polling:** Python polls `window.__scraperCompleted` and `window.__scraperDone`
every 1 second until the browser signals completion.

**HTML parsing:** Each fetched HTML string is parsed with `DOMParser`. Use a
`TreeWalker` with `NodeFilter.SHOW_TEXT` (value `4`) to collect **individual leaf text
nodes** from `[data-testid="atf-component"]`.

> ⚠️ **Critical:** Never use `.textContent` on the entire `atf-component`. Adjacent
> elements like `"7.0"` and `"2024"` get concatenated into `"7.02024"` with no separator,
> breaking all regex-based extraction. Always use TreeWalker to read text nodes one at a time.

---

## 5. Extracting Each Field from the Detail Page

The `[data-testid="atf-component"]` block contains all metadata. Its leaf text nodes
appear in this order (example from a real page):

```
"#1 in fantasy TV shows"
"Season 1"
"Set thousands of years before..."   ← description
"Star Filled" × N, "Star Empty" × M  ← star rating icons (ignore)
"29,971"                              ← IMDb review count
"IMDb 6.9"                            ← IMDb rating (combined OR separate)
"8 episodes"                          ← duration
"2022"                                ← year
"X-Ray" / "HDR" / "UHD"              ← badge nodes (ignore)
"TV-14"                               ← film/content rating
"Photosensitive" / "Subtitles Cc"    ← accessibility badges (ignore)
"Action" "•" "Adventure" "•" ...     ← genres + moods (•-separated)
"Play" / "Watch now" / ...            ← action buttons (stop here)
"Included with Prime"                 ← entitlement
```

### Field extraction rules

#### `is_prime` (filter flag — not in output)
```javascript
var entEl   = doc.querySelector('[data-testid="entitlement-message"]');
var entText = entEl ? entEl.textContent.trim() : '';
var is_prime = entText.startsWith('Included with Prime') &&
               !entText.toLowerCase().includes('subscription') &&
               !entText.toLowerCase().includes('channel');
```

#### `imdb_rating` + `imdb_review_count`
IMDb data appears in **two possible layouts** in the HTML:
- **(a) Separate nodes:** `"29,971"` | `"IMDb"` | `"6.9"`
- **(b) Combined node:** `"29,971"` | `"IMDb 6.9"`

Handle both. The review count is always the node **immediately before** the IMDb node.
Review count regex: `/^\d{1,3}(,\d{3})*$/` — matches `"21"`, `"2,864"`, `"29,971"`.
Strip commas before storing.

#### `duration`
Match text nodes exactly against:
- `/^\d{1,2}\s*h\s*\d{1,2}\s*min$/` → movies (e.g. `"2 h 5 min"`)
- `/^\d{1,2}\s*h$/` → movies without minutes (e.g. `"2h"`)
- `/^\d{1,3}\s*episodes?$/i` → TV shows (e.g. `"8 episodes"`)

#### `year`
Match text nodes against `/^(19|20)\d{2}$/`.

#### `film_rating`
Match text nodes exactly against this ordered list (check longer strings first):
`NC-17`, `TV-MA`, `TV-14`, `TV-PG`, `TV-G`, `TV-Y7`, `TV-Y`, `PG-13`, `NR`, `UR`, `R`, `PG`, `G`

#### `genres`
Do **NOT** use `[data-testid="genre-texts"]` — it only returns the FIRST genre.

Instead, locate the film rating node in `textNodes`, skip badge/accessibility nodes
after it, then collect all text until a stop-word is reached:

```
SKIP_AFTER_RATING = ['X-Ray','X-RAY','HDR','UHD','SDR','Photosensitive',
                     'Subtitles Cc','Audio Descriptions','Closed Captions']

STOP_WORDS = ['Play','Watch now','Go ad free','Watch trailer','Download',
              'Trailer','Add','Watchlist','Like','Not for me','Downloads',
              'Share','Share Android','Entitled','Included with Prime',
              'Terms apply','More purchase','options']
```

Collect all non-`•` nodes between SKIP and STOP. This gives ALL genre + mood tags
(e.g. `["Comedy", "Drama", "Heavy", "Heartwarming"]`).

#### `description`
1. Try `doc.querySelector('[data-testid="synopsis"], [class*="synopsis" i]')` — use `.textContent`
2. Fallback: find the longest text node before the `"IMDb"` node that:
   - Has length > 50 characters
   - Does not start with a digit or `#`
   - Does not start with `"Season"` or `"Episode"`

#### `show_title` (used to fix card names)
```javascript
var titleEl = doc.querySelector('h1, [data-testid="title-art"] img, [class*="titleArt" i] img');
var showTitle = (titleEl?.getAttribute('alt') || titleEl?.textContent || '').trim() || null;
```

---

## 6. Known Issues & Fixes

### Card titles that are just "Season X"
Some listing cards set `data-card-title` to `"Season 3"` instead of the full show name.
**Fix:** After fetching the detail page, if the card title matches
`/^(Season|Episode)\s+\d+/i`, replace it with the `show_title` extracted from the detail page.

### `textContent` concatenation bug
`element.textContent` collapses adjacent DOM elements into a single string with no
separator. `"7.0"` + `"2024"` becomes `"7.02024"`, breaking all number extraction.
**Fix:** Always use `TreeWalker` with `NodeFilter.SHOW_TEXT` to get individual text nodes.

### `innerText` does not work in DOMParser documents
`innerText` is a layout-dependent property. When HTML is parsed by `DOMParser` (not
rendered by the browser), `innerText` falls back to `textContent` behaviour — i.e. it
does not add layout-based line breaks. Use `textContent` on specific known elements,
and `TreeWalker` for the atf-component block.

### macOS Chrome remote debugging port (CDP) does not work
On macOS (Sonoma+), Chrome's App Sandbox prevents binding to the remote debugging TCP port
even with `--remote-debugging-port=9222`. Do not use Playwright's `connect_over_cdp()`.
Use AppleScript instead.

### Non-Prime content in listing pages
The listing URLs (`/gp/video/movie`, `/gp/video/tv`) show ALL content — including
rent/buy titles and channel add-on content. These must be filtered at the detail page
level using `[data-testid="entitlement-message"]`, not at the listing level.

---

## 7. File Structure

```
FindSomethingToWatch/
├── Find Me Something To Watch.md   # Original product idea / human instructions
├── AGENT_INSTRUCTIONS.md           # This file — full design guide for agents
├── requirements.txt                # No pip dependencies (pure stdlib + AppleScript)
├── scraper.py                      # Main scraper script (~224 lines, pure Python)
└── extractor.js                    # Browser-side JS for detail page parsing (~177 lines)
```

### Modularisation decision

`scraper.py` was originally ~400 lines with the JavaScript embedded as a Python string.
The JS was extracted into `extractor.js` for two reasons:
- JS in a `.js` file gets proper syntax highlighting and linting in any editor
- Each file now has a single responsibility (Python = pipeline control, JS = HTML parsing)

Everything else stays in one file. Do **not** split `scraper.py` further until a second
platform (e.g. Netflix) is added — the phases are sequential, interdependent, and not
reused elsewhere. Premature modularisation adds complexity with no benefit.

`scraper.py` loads the JS at runtime:
```python
BATCH_FETCH_JS = (Path(__file__).parent / "extractor.js").read_text()
```

Python then injects the collected URLs before execution:
```python
batch_js = BATCH_FETCH_JS.replace("URLS_PLACEHOLDER", json.dumps(urls))
```

### Escaping note
When the JS lived inside a Python triple-quoted string, all regex backslashes were
doubled (`\\d`, `\\s`, `\\u2022`). In `extractor.js` they are single backslashes
(`\d`, `\s`, `\u2022`) — standard JS. Do not re-introduce double backslashes.

---

## 8. Setup & Run Instructions

### Prerequisites
1. macOS with Google Chrome installed
2. Python 3.8+
3. Chrome open and logged into Amazon Prime Video

### One-time Chrome setup
Enable JavaScript from Apple Events:
> Chrome menu → **View → Developer → Allow JavaScript from Apple Events**

No pip packages are required. The tool uses only Python stdlib + system AppleScript.

### Running the scraper
```bash
# Test run — 10 movies + 10 TV shows
python3 FindSomethingToWatch/scraper.py --limit 10

# Production run — all available Prime content
python3 FindSomethingToWatch/scraper.py

# Custom limits
python3 FindSomethingToWatch/scraper.py --movie-limit 50 --tv-limit 100
```

Output is saved to `FindSomethingToWatch/prime_video_content.json`.

---

## 9. Future Work (Not Yet Implemented)

- **Netflix support** — requires a different scraping strategy (Netflix uses a heavily
  client-side rendered SPA; consider intercepting network requests or using a different
  page structure)
- **Duration for TV shows** — currently shows episode count; could be enhanced to show
  average episode runtime
- **Deduplication across platforms** — when Netflix is added, titles present on both
  platforms should be merged
- **Re-run / incremental updates** — only fetch detail pages for titles not already in
  the JSON file
