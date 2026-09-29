#!/usr/bin/env python3
"""Fetch quirky book awards from Wikipedia into the Best-Sellers cache schema.

Some literary "anti-awards" have no Goodreads /award/show page but keep a clean
winner table on Wikipedia (year / title / author). We read the wikitext via the
MediaWiki action API (the rendered HTML is rate-limited/blocked) and emit the
same JSON shape the plugin's Goodreads cache parser already consumes:

    {rank, title, authors, cover_url, source_url, blurb}

Output: gr/award_<slug>.json  (same directory as the Goodreads award caches)

NOTE: only awards whose winners are *books* are handled here. The Bulwer-Lytton
contest (worst opening sentence) and the Foot in Mouth award (spoken gaffes)
have person/quote winners with no book, so they are intentionally excluded.
The Bad Grammar Award is discontinued and is left out pending a strategy rethink.
"""
import json
import os
import re
import sys
import time

import requests

OUTPUT_DIR = 'gr'
API = 'https://en.wikipedia.org/w/api.php'
WIKI_UA = 'BestSellersCalibrePlugin/1.0 (awards research; +https://www.mobileread.com)'

# (slug, label, wikipedia_title, article_url)
WIKI_AWARDS = [
    ('diagram_prize', 'Diagram Prize for Oddest Title of the Year',
     'Diagram Prize',
     'https://en.wikipedia.org/wiki/Diagram_Prize_for_Oddest_Title_of_the_Year'),
]


def _fetch_wikitext(session, title):
    params = {'action': 'parse', 'page': title, 'prop': 'wikitext',
              'format': 'json', 'redirects': 1}
    r = session.get(API, params=params, timeout=25,
                    headers={'User-Agent': WIKI_UA})
    r.raise_for_status()
    data = r.json()
    if 'error' in data:
        raise RuntimeError(data['error'].get('code', 'api-error'))
    return data.get('parse', {}).get('wikitext', {}).get('*', '') or ''


def _strip_wiki(text):
    """Reduce a wikitext cell to plain text."""
    if not text:
        return ''
    # {{sortname|First|Last|...}} -> "First Last"
    def _sortname(m):
        parts = [p for p in m.group(1).split('|') if '=' not in p]
        return ' '.join(p.strip() for p in parts[:2] if p.strip())
    text = re.sub(r'\{\{\s*sortname\s*\|([^}]*)\}\}', _sortname, text, flags=re.I)
    # Drop <ref>...</ref> and self-closing refs
    text = re.sub(r'<ref[^>]*/>', '', text)
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.S)
    # [[link|display]] -> display ; [[link]] -> link
    text = re.sub(r'\[\[([^\]|]*)\|([^\]]*)\]\]', r'\2', text)
    text = re.sub(r'\[\[([^\]]*)\]\]', r'\1', text)
    # Remaining templates {{...}} -> inner best-effort
    text = re.sub(r'\{\{[^}]*\}\}', '', text)
    # Bold/italic markup and stray tags
    text = re.sub(r"'{2,}", '', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&nbsp;', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def _parse_diagram(wikitext):
    """Parse the Diagram Prize wikitable (Year | Title | Author/Editor | Publisher)."""
    idx = wikitext.find('{|')
    if idx < 0:
        return []
    end = wikitext.find('|}', idx)
    table = wikitext[idx:end if end > 0 else len(wikitext)]

    books = []
    # Rows are separated by "|-". Each data row holds year (header cell) then
    # title / author / publisher cells.
    rows = re.split(r'\n\|-', table)
    for row in rows:
        # Year comes from a "!scope=row|YYYY" header cell.
        ym = re.search(r'!\s*scope=row\s*\|\s*(\d{4})', row)
        if not ym:
            continue
        year = ym.group(1)
        # Data cells start with a line beginning "|" (not "|-", not "|+").
        cells = []
        for line in row.splitlines():
            line = line.strip()
            if line.startswith('|') and not line.startswith('|-') and not line.startswith('|+'):
                cells.append(line[1:].strip())
        if len(cells) < 2:
            continue
        title = _strip_wiki(cells[0])
        author = _strip_wiki(cells[1]) if len(cells) > 1 else ''
        publisher = _strip_wiki(cells[2]) if len(cells) > 2 else ''
        if not title:
            continue
        books.append({
            'rank': str(len(books) + 1),
            'title': title,
            'authors': author or '\u2014',
            'cover_url': '',
            'source_url': 'https://en.wikipedia.org/wiki/Diagram_Prize_for_Oddest_Title_of_the_Year',
            'blurb': 'Diagram Prize winner ({}){}'.format(
                year, ' — ' + publisher if publisher else ''),
        })
    return books


PARSERS = {
    'diagram_prize': _parse_diagram,
}


def _write(slug, books):
    path = os.path.join(OUTPUT_DIR, 'award_{}.json'.format(slug))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(books, f, ensure_ascii=False, indent=2)
    return path


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = requests.Session()
    for slug, label, title, _url in WIKI_AWARDS:
        print('Fetching {} (Wikipedia:{})'.format(label, title))
        try:
            wt = _fetch_wikitext(session, title)
            parser = PARSERS.get(slug)
            books = parser(wt) if parser else []
            path = _write(slug, books)
            print('  {} books -> {}'.format(len(books), path))
        except Exception as e:  # noqa: BLE001
            print('  ERROR: {}'.format(e), file=sys.stderr)
            _write(slug, [])
        time.sleep(0.5)


if __name__ == '__main__':
    sys.exit(main())
