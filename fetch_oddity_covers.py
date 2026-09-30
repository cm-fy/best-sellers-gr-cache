#!/usr/bin/env python3
"""One-off: fetch covers for Wikipedia-sourced oddity awards and host them here.

The Diagram Prize winners come from a Wikipedia table with no cover images, and
they match Open Library title search poorly.  This script searches Open Library
by title (and title+author), downloads any cover it finds into
``gr/covers/<slug>/<rank>.jpg``, and rewrites the award JSON's ``cover_url`` to
the GitHub Pages URL that serves that file.  Titles with no match keep an empty
``cover_url`` (the plugin then shows the default-cover placeholder).

Safe to re-run: existing files are reused, only missing covers are fetched.
"""
import json
import os
import time
import urllib.parse
import urllib.request

SLUGS = ['diagram_prize']
PAGES_BASE = 'https://cm-fy.github.io/best-sellers-gr-cache'
UA = {'User-Agent': 'Mozilla/5.0 (Best-Sellers cover fetcher)'}
GENERIC_AUTHORS = ('', '\u2014', 'Various authors')


def _title_variants(title):
    """Yield progressively looser title forms to widen the OL match."""
    seen = []
    def _add(t):
        t = (t or '').strip().strip('–-:').strip()
        if t and t not in seen:
            seen.append(t)
    _add(title)
    # Drop a trailing subtitle after the first colon / en dash.
    for sep in (':', ' – ', ' - '):
        if sep in title:
            _add(title.split(sep, 1)[0])
    return seen


def _search(qs):
    api = 'https://openlibrary.org/search.json?{}&limit=5'.format(qs)
    try:
        req = urllib.request.Request(api, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001
        print('    search error:', e)
        return None
    for doc in data.get('docs') or []:
        cid = doc.get('cover_i') or doc.get('cover_id')
        if cid:
            return cid
    return None


def _ol_cover_id(title, author):
    author_q = ''
    if author and author not in GENERIC_AUTHORS:
        author_q = urllib.parse.quote_plus(author.split(',')[0])
    for variant in _title_variants(title):
        tq = urllib.parse.quote_plus(variant)
        if author_q:
            cid = _search('title={}&author={}'.format(tq, author_q))
            if cid:
                return cid
            time.sleep(0.3)
        cid = _search('title={}'.format(tq))
        if cid:
            return cid
        time.sleep(0.3)
    # Last resort: general full-text query (title + author in q=).
    q = title
    if author and author not in GENERIC_AUTHORS:
        q += ' ' + author.split(',')[0]
    return _search('q={}'.format(urllib.parse.quote_plus(q)))


def _download(cover_id, dest):
    url = 'https://covers.openlibrary.org/b/id/{}-M.jpg'.format(cover_id)
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        # Open Library serves a 1x1 / tiny blank for missing covers.
        if not data or len(data) < 1000:
            return False
        with open(dest, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:  # noqa: BLE001
        print('    download error:', e)
        return False


def process(slug):
    path = os.path.join('gr', 'award_{}.json'.format(slug))
    with open(path, encoding='utf-8') as f:
        books = json.load(f)
    cover_dir = os.path.join('gr', 'covers', slug)
    os.makedirs(cover_dir, exist_ok=True)
    hits = 0
    for b in books:
        rank = str(b.get('rank') or '').strip() or '0'
        fname = '{}.jpg'.format(rank)
        dest = os.path.join(cover_dir, fname)
        pages_url = '{}/gr/covers/{}/{}'.format(PAGES_BASE, slug, fname)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            b['cover_url'] = pages_url
            hits += 1
            continue
        cid = _ol_cover_id(b.get('title', ''), b.get('authors', ''))
        if cid and _download(cid, dest):
            b['cover_url'] = pages_url
            hits += 1
            print('  OK  {}  {}'.format(rank, b.get('title', '')[:50]))
        else:
            b['cover_url'] = ''
            print('  --  {}  {}'.format(rank, b.get('title', '')[:50]))
        time.sleep(0.4)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(books, f, ensure_ascii=False, indent=2)
    print('{}: {}/{} covers hosted'.format(slug, hits, len(books)))


def main():
    for slug in SLUGS:
        process(slug)


if __name__ == '__main__':
    main()
