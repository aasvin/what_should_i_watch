(function(urls) {
    window.__scraperResults   = {};
    window.__scraperCompleted = 0;
    window.__scraperDone      = false;

    function parseHtml(html) {
        var parser = new DOMParser();
        var doc    = parser.parseFromString(html, 'text/html');

        // ── Is this included with Prime? ──────────────────────────────────────
        var entEl   = doc.querySelector('[data-testid="entitlement-message"]');
        var entText = entEl ? entEl.textContent.trim() : '';
        // Keep only content where the entitlement is exactly Prime (not a channel
        // add-on like "Included with your Peacock subscription").
        var is_prime = entText.startsWith('Included with Prime') &&
                       !entText.toLowerCase().includes('subscription') &&
                       !entText.toLowerCase().includes('channel');

        // ── Collect leaf text nodes inside atf-component ─────────────────────
        // We use individual text nodes (not .textContent) so adjacent numbers
        // like "7.0" and "2024" never get concatenated into "7.02024".
        var atfEl     = doc.querySelector('[data-testid="atf-component"]');
        var textNodes = [];
        if (atfEl) {
            var walker = doc.createTreeWalker(atfEl, 4 /* NodeFilter.SHOW_TEXT */);
            var node;
            while ((node = walker.nextNode())) {
                var t = node.textContent.trim();
                if (t) textNodes.push(t);
            }
        }

        // ── IMDb rating & review count ────────────────────────────────────────
        // Two possible node layouts from Amazon's HTML:
        //   (a) separate nodes: "29,971"  |  "IMDb"  |  "6.9"
        //   (b) combined node:  "29,971"  |  "IMDb 6.9"
        // In both cases the review count is in the node immediately before IMDb.
        var imdb_rating       = 'N/A';
        var imdb_review_count = 'N/A';
        for (var i = 0; i < textNodes.length; i++) {
            if (textNodes[i] === 'IMDb' && i + 1 < textNodes.length) {
                // Layout (a)
                var cand = textNodes[i + 1].trim();
                if (/^\d+\.\d+$/.test(cand)) imdb_rating = cand;
                if (i > 0 && /^\d{1,3}(,\d{3})*$/.test(textNodes[i - 1])) {
                    imdb_review_count = textNodes[i - 1].replace(/,/g, '');
                }
                break;
            }
            var m = textNodes[i].match(/^IMDb\s+(\d+\.\d+)$/);
            if (m) {
                // Layout (b)
                imdb_rating = m[1];
                if (i > 0 && /^\d{1,3}(,\d{3})*$/.test(textNodes[i - 1])) {
                    imdb_review_count = textNodes[i - 1].replace(/,/g, '');
                }
                break;
            }
        }

        // ── Runtime ───────────────────────────────────────────────────────────
        var duration = 'N/A';
        for (var j = 0; j < textNodes.length; j++) {
            var tn = textNodes[j];
            if (/^\d{1,2}\s*h\s*\d{1,2}\s*min$/.test(tn) ||
                /^\d{1,2}\s*h$/.test(tn) ||
                /^\d{1,3}\s*episodes?$/i.test(tn)) {
                duration = tn.replace(/\s+/g, ' ');
                break;
            }
        }

        // ── Year ──────────────────────────────────────────────────────────────
        var year = 'N/A';
        for (var yi = 0; yi < textNodes.length; yi++) {
            if (/^(19|20)\d{2}$/.test(textNodes[yi])) {
                year = textNodes[yi];
                break;
            }
        }

        // ── Film/content rating (R, PG-13, TV-14, etc.) ───────────────────────
        var RATINGS = ['NC-17','TV-MA','TV-14','TV-PG','TV-G','TV-Y7','TV-Y',
                       'PG-13','NR','UR','R','PG','G'];
        var film_rating = 'N/A';
        for (var ri = 0; ri < textNodes.length; ri++) {
            if (RATINGS.indexOf(textNodes[ri]) !== -1) {
                film_rating = textNodes[ri];
                break;
            }
        }

        // ── Genres (all tags from the •-separated sequence after film rating) ───
        // Amazon's genre-texts element only holds the FIRST genre. The complete
        // list (all genres + moods like "Comedy • Drama • Heavy • Heartwarming")
        // lives in the atfNodes right after the film-rating and badge nodes.
        var genres = [];
        var SKIP_AFTER_RATING = ['X-Ray','X-RAY','HDR','UHD','SDR','Photosensitive',
                                 'Subtitles Cc','Audio Descriptions','Closed Captions'];
        var STOP_WORDS = ['Play','Watch now','Go ad free','Watch trailer','Download',
                          'Trailer','Add','Watchlist','Like','Not for me','Downloads',
                          'Share','Share Android','Entitled','Included with Prime',
                          'Terms apply','More purchase','options'];
        var ratingIdx = -1;
        for (var ri2 = 0; ri2 < textNodes.length; ri2++) {
            if (RATINGS.indexOf(textNodes[ri2]) !== -1) { ratingIdx = ri2; break; }
        }
        if (ratingIdx >= 0) {
            var gStart = ratingIdx + 1;
            // Skip over badge/accessibility nodes immediately after the rating
            while (gStart < textNodes.length && SKIP_AFTER_RATING.indexOf(textNodes[gStart]) !== -1) {
                gStart++;
            }
            for (var gi = gStart; gi < textNodes.length; gi++) {
                var gt = textNodes[gi];
                if (gt === '\u2022' || gt === '•') continue;
                if (STOP_WORDS.indexOf(gt) !== -1) break;
                genres.push(gt);
            }
        }

        // ── Description ───────────────────────────────────────────────────────
        var descEl      = doc.querySelector('[data-testid="synopsis"], [class*="synopsis" i]');
        var description = 'N/A';
        if (descEl) {
            description = descEl.textContent.trim();
        } else {
            var imdbIdx = textNodes.indexOf('IMDb');
            var pool    = imdbIdx > 0 ? textNodes.slice(0, imdbIdx) : textNodes.slice(0, 20);
            for (var k = pool.length - 1; k >= 0; k--) {
                var ln = pool[k];
                if (ln.length > 50 && !/^[\d#]/.test(ln) && !/^Season/i.test(ln)) {
                    description = ln; break;
                }
            }
        }

        // ── Show title (fixes cards named just "Season X") ────────────────────
        var titleEl   = doc.querySelector('h1, [data-testid="title-art"] img, [class*="titleArt" i] img');
        var showTitle = null;
        if (titleEl) {
            showTitle = (titleEl.getAttribute('alt') || titleEl.textContent || '').trim() || null;
        }

        return {
            show_title:        showTitle,
            is_prime:          is_prime,
            description:       description,
            imdb_rating:       imdb_rating,
            imdb_review_count: imdb_review_count,
            duration:          duration,
            year:              year,
            film_rating:       film_rating,
            genres:            genres,
        };
    }

    // Process in batches of 8 with a 300 ms gap to be polite to Amazon's servers
    var BATCH = 8;

    function runBatch(start) {
        var slice = urls.slice(start, start + BATCH);
        if (slice.length === 0) { window.__scraperDone = true; return; }
        Promise.all(slice.map(function(url) {
            return fetch(url, { credentials: 'include' })
                .then(function(r) { return r.text(); })
                .then(function(html) { window.__scraperResults[url] = parseHtml(html); })
                .catch(function(e)  { window.__scraperResults[url] = { error: e.message }; })
                .finally(function() { window.__scraperCompleted++; });
        })).then(function() {
            setTimeout(function() { runBatch(start + BATCH); }, 300);
        });
    }

    runBatch(0);
    return 'started';
})(URLS_PLACEHOLDER)
