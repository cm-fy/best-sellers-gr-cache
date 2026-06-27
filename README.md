# Best-Sellers Goodreads Cache

Static cached Goodreads list data, updated weekly via GitHub Actions.

## Data format

- `gr/meta.json` — metadata about all cached lists (slugs, labels, book counts, status)
- `gr/most_read.json` — Most Read list
- `gr/popular_month.json` — Popular This Month (current month)
- `gr/popular_year.json` — Popular This Year (current year)
- `gr/best_books_ever.json` — Best Books Ever
- `gr/hugo_award.json` — Hugo Award
- `gr/shelf_{name}.json` — One file per shelf (adventure, fantasy, etc.)

## Usage

GitHub Pages URL pattern:

```
https://cm-fy.github.io/best-sellers-gr-cache/gr/{slug}.json
```

## Data freshness

Lists are fetched every Monday at 02:00 UTC.
Manual triggers are also available via the Actions tab.

## Anti-bot notes

Goodreads may serve captcha pages. The scraper detects these and records
the status in `meta.json`. If a list gets captcha'd, an empty JSON array
is written so the plugin can fall back to direct fetching.
