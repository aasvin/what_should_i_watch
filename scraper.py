import argparse
import re
import subprocess
import json
import time
from pathlib import Path

OUTPUT_FILE = Path(__file__).parent / "prime_video_content.json"

LISTING_URLS = {
    "Movie": "https://www.amazon.com/gp/video/movie",
    "TV Show": "https://www.amazon.com/gp/video/tv",
}

# Phase 1: Extract cards + detail URLs from listing pages
LISTING_JS = """
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
"""

# Phase 2: JS loaded from extractor.js at runtime
BATCH_FETCH_JS = (Path(__file__).parent / "extractor.js").read_text()


# ── Helpers ───────────────────────────────────────────────────────────────────

def run_applescript(script):
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def execute_js(js_code):
    """Execute JS in Chrome's active tab via clipboard (avoids escaping issues)."""
    subprocess.run(["pbcopy"], input=js_code.encode(), check=True)
    return run_applescript("""
    tell application "Google Chrome"
        set jsCode to the clipboard
        return execute active tab of front window javascript jsCode
    end tell
    """)


def navigate_to(url):
    run_applescript(f"""
    tell application "Google Chrome"
        set URL of active tab of front window to "{url}"
    end tell
    """)
    time.sleep(4)


def scroll_to_bottom(max_scrolls=25):
    prev_height = 0
    for i in range(max_scrolls):
        raw = execute_js(
            "window.scrollBy(0, window.innerHeight * 2); document.body.scrollHeight;"
        )
        try:
            height = int(raw)
        except (ValueError, TypeError):
            height = 0
        time.sleep(1.5)
        if height == prev_height:
            print(f"  Reached bottom after {i + 1} scroll(s).")
            break
        prev_height = height


# ── Phase 1: Scrape listing pages ─────────────────────────────────────────────

def scrape_listings(movie_limit=None, tv_limit=None):
    all_items = []
    seen = set()

    sections = [
        ("Movies",   "Movie",   movie_limit),
        ("TV Shows", "TV Show", tv_limit),
    ]

    for label, content_type, limit in sections:
        print(f"\n--- {label} (limit: {limit if limit else 'all'}) ---")
        navigate_to(LISTING_URLS[content_type])
        print("  Scrolling...")
        scroll_to_bottom()

        raw = execute_js(LISTING_JS)
        try:
            items = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            print(f"  Warning: could not parse listing. Got: {str(raw)[:200]}")
            items = []

        added = 0
        for item in items:
            if limit is not None and added >= limit:
                break
            if item["name"] not in seen:
                seen.add(item["name"])
                all_items.append(item)
                added += 1

        print(f"  Collected {added} titles ({len(all_items)} unique total).")

    return all_items


# ── Phase 2: Batch-fetch detail pages ─────────────────────────────────────────

def fetch_all_details(items):
    urls = [item["detail_url"] for item in items if item.get("detail_url")]
    total = len(urls)
    print(f"\nFetching details for {total} titles (running in browser)...")

    batch_js = BATCH_FETCH_JS.replace("URLS_PLACEHOLDER", json.dumps(urls))
    execute_js(batch_js)

    while True:
        try:
            completed = int(execute_js("window.__scraperCompleted || 0"))
            done = execute_js("window.__scraperDone ? 'true' : 'false'") == "true"
        except Exception:
            completed, done = 0, False

        print(f"\r  Progress: {completed}/{total}", end="", flush=True)
        if done:
            break
        time.sleep(1)

    print(f"\r  Progress: {total}/{total} — done!              ")

    raw = execute_js("JSON.stringify(window.__scraperResults)")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Prime Video content scraper")
    parser.add_argument("--movie-limit", type=int, default=None,
                        help="Max number of movies to collect (default: all)")
    parser.add_argument("--tv-limit", type=int, default=None,
                        help="Max number of TV shows to collect (default: all)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Same limit for both movies and TV shows")
    args = parser.parse_args()

    movie_limit = args.movie_limit or args.limit
    tv_limit    = args.tv_limit    or args.limit

    print("=" * 50)
    print("  Prime Video Content Scraper")
    print("=" * 50)

    try:
        current_url = run_applescript(
            'tell application "Google Chrome" to return URL of active tab of front window'
        )
        print(f"\nConnected to Chrome. Tab: {current_url}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return

    # Phase 1 — collect listings
    items = scrape_listings(movie_limit=movie_limit, tv_limit=tv_limit)

    # Phase 2 — enrich with detail page data
    detail_map = fetch_all_details(items)

    # Merge + filter
    enriched = []
    skipped  = 0
    for item in items:
        url    = item.pop("detail_url", None)
        detail = detail_map.get(url, {})

        # Skip non-Prime content (rent/buy or channel subscriptions)
        if not detail.get("is_prime", False):
            skipped += 1
            continue

        # Fix card titles that are just "Season X" / "Episode X"
        if re.match(r'^(Season|Episode)\s+\d+', item["name"], re.IGNORECASE):
            show_title = detail.get("show_title")
            if show_title:
                item["name"] = show_title

        item["description"]       = detail.get("description",       "N/A")
        item["imdb_rating"]       = detail.get("imdb_rating",       "N/A")
        item["imdb_review_count"] = detail.get("imdb_review_count", "N/A")
        item["duration"]          = detail.get("duration",          "N/A")
        item["year"]              = detail.get("year",              "N/A")
        item["film_rating"]       = detail.get("film_rating",       "N/A")
        item["genres"]            = detail.get("genres",            [])

        enriched.append(item)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(enriched)} Prime titles ({skipped} non-Prime skipped) to:\n  {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
