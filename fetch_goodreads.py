#!/usr/bin/env python3
"""Fetch Goodreads best-of lists and cache as JSON for the Best-Sellers calibre plugin.

Scrapes Goodreads HTML pages, extracts book data (rank, title, authors, cover URL,
source URL), and writes structured JSON files to the gr/ directory.

Goodreads has no public API for list/shelf data, so we scrape the server-rendered
HTML.  The site occasionally serves captcha pages; we detect those and report them
in meta.json so the plugin can fall back gracefully.

Output structure:
    gr/meta.json          — metadata about all cached lists
    gr/most_read.json     — Most Read list
    gr/popular_month.json — Popular This Month (current month)
    gr/popular_year.json  — Popular This Year (current year)
    gr/best_books_ever.json — Best Books Ever
    gr/hugo_award.json    — Hugo Award
    gr/shelf_{slug}.json  — One file per shelf (adventure, fantasy, etc.)
"""

import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Configuration ──────────────────────────────────────────────────────────────

OUTPUT_DIR = 'gr'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': (
        'text/html,application/xhtml+xml,application/xml;'
        'q=0.9,image/avif,image/webp,*/*;q=0.8'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

# Goodreads lists to scrape — (slug, label, url)
# Callable URLs (gr_month, gr_year) are computed dynamically below.
NOW = datetime.utcnow()

SHELVES = [
    'adventure',
    'fantasy',
    'science-fiction',
    'historical-fiction',
    'romance',
    'thriller',
    'horror',
    'young-adult',
    'non-fiction',
    'currently-reading',
]

STATIC_LISTS = [
    ('most_read',     'Most Read',           'https://www.goodreads.com/book/most_read'),
    ('best_books_ever', 'Best Books Ever',    'https://www.goodreads.com/list/show/1.Best_Books_Ever'),
    ('hugo_award',    'Hugo Award',           'https://www.goodreads.com/award/show/9-hugo-award'),
]

DYNAMIC_LISTS = [
    ('popular_month', 'Popular This Month',
     f'https://www.goodreads.com/book/popular_by_date/{NOW.year}/{NOW.month}'),
    ('popular_year',  'Popular This Year',
     f'https://www.goodreads.com/book/popular_by_date/{NOW.year}/'),
]

SESSION_COOKIE = os.environ.get('GR_SESSION_COOKIE', '')


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _session():
    s = requests.Session()
    s.headers.update(HEADERS)
    if SESSION_COOKIE:
        # Accept raw cookie string like "key1=val1; key2=val2"
        s.headers['Cookie'] = SESSION_COOKIE
    return s


def _fetch(session, url, retries=2):
    """Fetch a URL, returning (html, is_captcha)."""
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            html = r.text
            lower = html.lower()
            if any(s in lower for s in ('captcha', 'robot check', 'are you a human')):
                return html, True
            return html, False
        except requests.RequestException as e:
            if attempt == retries:
                raise
            # Brief pause before retry
            import time
            time.sleep(3)
    return '', True


# ── Parsers ────────────────────────────────────────────────────────────────────

def _decode_html(text):
    """Decode HTML entities to plain unicode."""
    try:
        from html import unescape
        return unescape(text or '')
    except Exception:
        return text or ''


def _parse_table_rows(html):
    """Parse the classic Goodreads table layout (bookTitle class in <tr> rows)."""
    books = []
    rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', html, re.DOTALL | re.I)
    if not rows or not any('bookTitle' in r for r in rows):
        return None  # Not this layout

    for row in rows:
        if 'bookTitle' not in row and '/book/show/' not in row:
            continue
        m = (re.search(
            r'<a[^>]+class="bookTitle"[^>]*href="([^"]+)"[^>]*>\s*(?:<span[^>]*>)?([^<]+?)(?:</span>)?\s*</a>',
            row, re.I) or
             re.search(r'<a[^>]+href="([^"]*/book/show/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
                       row, re.I))
        if not m:
            continue
        path = m.group(1)
        title = re.sub(r'\s*\([^)]+\)\s*$', '', _decode_html(m.group(2).strip()))
        if not title:
            continue
        src_url = path if path.startswith('http') else 'https://www.goodreads.com' + path
        authors = re.findall(
            r'<a[^>]+class="authorName"[^>]*>\s*(?:<span[^>]*>)?([^<]+?)(?:</span>)?\s*</a>',
            row, re.I)
        cover_url = ''
        cm = re.search(r'<img[^>]+src="(https?://[^"]+)"', row, re.I)
        if cm:
            cover_url = re.sub(r'\._S[XY]\d+_', '._SY200_', cm.group(1))
        books.append({
            'rank':       str(len(books) + 1),
            'title':      title,
            'authors':    ', '.join(a.strip() for a in authors if a.strip()) or '\u2014',
            'cover_url':  cover_url,
            'source_url': src_url,
        })
    return books


def _parse_ranked_headings(html):
    """Parse the #1, #2, ... heading layout (e.g. Best Books Ever)."""
    books = []
    for m in re.finditer(r'<h2[^>]*>\s*#(\d+)\s*</h2>', html, re.DOTALL | re.I):
        start = m.end()
        next_rank = re.search(r'<h2[^>]*>\s*#\d+\s*</h2>', html[start:], re.DOTALL | re.I)
        end = start + next_rank.start() if next_rank else len(html)
        row = html[start:end]
        book = re.search(r'<a[^>]+href="([^"]*/book/show/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
                         row, re.DOTALL | re.I)
        if not book:
            continue
        path = book.group(1)
        title = re.sub(r'\s*\([^)]+\)\s*$', '', _decode_html(book.group(2).strip()))
        if not title:
            continue
        src_url = path if path.startswith('http') else 'https://www.goodreads.com' + path
        authors = []
        for author in re.findall(r'<a[^>]+href="[^"]*/author/show/[^"]+"[^>]*>\s*([^<]+?)\s*</a>',
                                 row, re.DOTALL | re.I):
            author = re.sub(r'\s+Goodreads Author\s*$', '', _decode_html(author).strip())
            if author and author not in authors:
                authors.append(author)
        cover_url = ''
        cm = re.search(r'<img[^>]+src="(https?://[^"]+)"', row, re.I)
        if cm:
            cover_url = re.sub(r'\._S[XY]\d+_', '._SY200_', cm.group(1))
        books.append({
            'rank':       m.group(1),
            'title':      title,
            'authors':    ', '.join(authors) or '\u2014',
            'cover_url':  cover_url,
            'source_url': src_url,
        })
        if len(books) >= 50:
            break
    return books


def _parse_shelf_cards(html):
    """Parse shelf/list pages that use card-style divs (modern Goodreads layout).

    Handles both the older 'left' class divs and newer card layouts.
    """
    books = []

    # Try BeautifulSoup-based parsing for robustness
    soup = BeautifulSoup(html, 'lxml')

    # Strategy 1: Find book links with /book/show/ pattern
    for link in soup.find_all('a', href=re.compile(r'/book/show/\d+')):
        href = link.get('href', '')
        title_text = link.get_text(strip=True)
        if not title_text:
            continue
        title = re.sub(r'\s*\([^)]+\)\s*$', '', _decode_html(title_text))
        if not title:
            continue
        src_url = href if href.startswith('http') else 'https://www.goodreads.com' + href

        # Find the parent container to extract author and cover
        parent = link.find_parent(['div', 'tr', 'li'])
        authors = []
        cover_url = ''

        if parent:
            # Author links
            for a_tag in parent.find_all('a', href=re.compile(r'/author/show/')):
                author_name = a_tag.get_text(strip=True)
                author_name = re.sub(r'\s+Goodreads Author\s*$', '', _decode_html(author_name))
                if author_name and author_name not in authors:
                    authors.append(author_name)
            # Cover image
            img = parent.find('img', src=re.compile(r'https?://'))
            if img:
                src = img.get('src', '')
                cover_url = re.sub(r'\._S[XY]\d+_', '._SY200_', src)

        books.append({
            'rank':       str(len(books) + 1),
            'title':      title,
            'authors':    ', '.join(authors) or '\u2014',
            'cover_url':  cover_url,
            'source_url': src_url,
        })
        if len(books) >= 50:
            break

    return books


def parse_goodreads(html):
    """Parse Goodreads HTML using the same multi-strategy approach as the plugin."""
    # Strategy 1: table rows with bookTitle class
    result = _parse_table_rows(html)
    if result:
        return result

    # Strategy 2: ranked headings (#1, #2, ...)
    result = _parse_ranked_headings(html)
    if result:
        return result

    # Strategy 3: card/shelf layout (BeautifulSoup)
    result = _parse_shelf_cards(html)
    if result:
        return result

    return []


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    session = _session()
    meta = {
        'fetched_at': NOW.isoformat() + 'Z',
        'year': NOW.year,
        'month': NOW.month,
        'lists': [],
    }

    all_lists = STATIC_LISTS + DYNAMIC_LISTS
    for slug, label, url in all_lists:
        print(f'Fetching {label}: {url}')
        try:
            html, is_captcha = _fetch(session, url)
            if is_captcha:
                print(f'  CAPTCHA detected for {label} — skipping')
                meta['lists'].append({
                    'slug': slug,
                    'label': label,
                    'url': url,
                    'book_count': 0,
                    'status': 'captcha',
                })
                # Write empty list so plugin knows it was attempted
                _write(slug, [])
                continue
            books = parse_goodreads(html)
            _write(slug, books)
            print(f'  {len(books)} books parsed')
            meta['lists'].append({
                'slug': slug,
                'label': label,
                'url': url,
                'book_count': len(books),
                'status': 'ok' if books else 'empty',
            })
        except Exception as e:
            print(f'  ERROR: {e}', file=sys.stderr)
            _write(slug, [])
            meta['lists'].append({
                'slug': slug,
                'label': label,
                'url': url,
                'book_count': 0,
                'status': f'error: {e}',
            })

    for shelf in SHELVES:
        slug = f'shelf_{shelf}'
        label = shelf.replace('-', ' ').title()
        url = f'https://www.goodreads.com/shelf/show/{shelf}'
        print(f'Fetching shelf {label}: {url}')
        try:
            html, is_captcha = _fetch(session, url)
            if is_captcha:
                print(f'  CAPTCHA detected for {label} — skipping')
                meta['lists'].append({
                    'slug': slug,
                    'label': label,
                    'url': url,
                    'book_count': 0,
                    'status': 'captcha',
                })
                _write(slug, [])
                continue
            books = parse_goodreads(html)
            _write(slug, books)
            print(f'  {len(books)} books parsed')
            meta['lists'].append({
                'slug': slug,
                'label': label,
                'url': url,
                'book_count': len(books),
                'status': 'ok' if books else 'empty',
            })
        except Exception as e:
            print(f'  ERROR: {e}', file=sys.stderr)
            _write(slug, [])
            meta['lists'].append({
                'slug': slug,
                'label': label,
                'url': url,
                'book_count': 0,
                'status': f'error: {e}',
            })

    # Write meta
    meta_path = os.path.join(OUTPUT_DIR, 'meta.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f'\nMeta written: {len(meta["lists"])} lists, {sum(l["book_count"] for l in meta["lists"])} total books')


def _write(slug, books):
    path = os.path.join(OUTPUT_DIR, f'{slug}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(books, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
