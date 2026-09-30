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
    ('wodehouse', 'Bollinger Everyman Wodehouse Prize',
     'Bollinger Everyman Wodehouse Prize',
     'https://en.wikipedia.org/wiki/Bollinger_Everyman_Wodehouse_Prize'),
    ('prix_page_111', 'Prix de la page 111 (France)',
     'Prix de la page 111',
     'https://fr.wikipedia.org/wiki/Prix_de_la_page_111'),
    # Current-edition pages (year-specific), not historical winners.
    ('booker_2026', 'Booker Prize 2026 (longlist & shortlist)',
     '2026 Booker Prize',
     'https://en.wikipedia.org/wiki/2026_Booker_Prize'),
]

# Prix de la page 111 lives on the French Wikipedia; everything else on English.
_WIKI_API_FOR = {
    'prix_page_111': 'https://fr.wikipedia.org/w/api.php',
}


def _fetch_wikitext(session, title, api=API):
    params = {'action': 'parse', 'page': title, 'prop': 'wikitext',
              'format': 'json', 'redirects': 1}
    r = session.get(api, params=params, timeout=25,
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


def _sortname_to_name(cell):
    """Turn a {{sortname|last=X|first=Y}} / {{sortname|First|Last}} cell into a
    plain "First Last" string, then strip any remaining wiki markup."""
    m = re.search(r'\{\{\s*sortname\s*\|([^}]*)\}\}', cell, re.I)
    if m:
        first = last = ''
        positional = []
        for part in m.group(1).split('|'):
            part = part.strip()
            if part.lower().startswith('first'):
                first = part.split('=', 1)[1].strip()
            elif part.lower().startswith('last'):
                last = part.split('=', 1)[1].strip()
            elif part.lower().startswith('dab'):
                continue
            elif '=' not in part:
                positional.append(part)
        if first or last:
            name = (first + ' ' + last).strip()
        else:
            name = ' '.join(positional[:2]).strip()
        # Some rows chain a second author after the template ("& [[Foo|Bar]]").
        rest = cell[m.end():]
        rest = _strip_wiki(rest)
        if rest:
            name = (name + ' ' + rest).strip()
        return _strip_wiki(name)
    return _strip_wiki(cell)


def _clean_title_cell(cell):
    """Extract a book title from a wikitext table cell, unwrapping {{sort}}.

    The display value may itself contain a wikilink with a pipe
    (``{{sort|1=Rotters' Club|2=[[The Rotters' Club (novel)|The Rotters' Club]]}}``),
    so we cannot naively split the template body on ``|``. We take everything
    after ``2=`` (the display form is always the last named parameter)."""
    sm = re.search(r'\{\{\s*sort\s*\|(.*?)\}\}', cell, re.I | re.S)
    if sm:
        inner = sm.group(1)
        if '2=' in inner:
            disp = inner.split('2=', 1)[1].strip()
        elif '1=' in inner:
            disp = inner.split('1=', 1)[1].strip()
        else:
            # {{sort|key|display}} positional: display is the last segment, but
            # keep any bracketed link intact by splitting only on top-level pipes.
            disp = re.split(r'\|(?![^\[]*\])', inner)[-1].strip()
        cell = disp or cell
    return _strip_wiki(cell)


def _parse_wodehouse(wikitext):
    """Parse the Bollinger Everyman Wodehouse Prize winners table.

    The table lists every finalist with a Result column; we keep only the
    'Winner' rows. Year is carried in a header cell (possibly rowspanned), so
    it persists across the shortlisted rows until the next year header."""
    idx = wikitext.find('==Winners')
    if idx < 0:
        idx = wikitext.find('== Winners')
    start = wikitext.find('{|', idx if idx >= 0 else 0)
    if start < 0:
        return []
    end = wikitext.find('|}', start)
    table = wikitext[start:end if end > 0 else len(wikitext)]

    books = []
    current_year = ''
    rows = re.split(r'\n\|-', table)
    for row in rows:
        # Year header cell: "! rowspan=5 |[[2000 in literature|2000]]" or "![[2001...".
        ym = re.search(r'!\s*(?:rowspan="?\d+"?\s*\|)?\s*\[\[(\d{4})\b', row)
        if ym:
            current_year = ym.group(1)
        if not re.search(r'\bWinner\b', row):
            continue
        # Data cells begin lines with "|" (skip "|-", "|+", and header "!").
        cells = []
        for line in row.splitlines():
            s = line.strip()
            if s.startswith('|') and not s.startswith('|-') and not s.startswith('|+'):
                cells.append(s[1:].strip())
        if len(cells) < 2:
            continue
        author = _sortname_to_name(cells[0])
        title = _clean_title_cell(cells[1])
        publisher = _strip_wiki(cells[2]) if len(cells) > 2 else ''
        if not title:
            continue
        books.append({
            'rank': str(len(books) + 1),
            'title': title,
            'authors': author or '\u2014',
            'cover_url': '',
            'source_url': 'https://en.wikipedia.org/wiki/Bollinger_Everyman_Wodehouse_Prize',
            'blurb': 'Wodehouse Prize winner ({}){}'.format(
                current_year, ' — ' + publisher if publisher else ''),
        })
    return books


def _parse_prix_page_111(wikitext):
    """Parse the Prix de la page 111 winners list (French Wikipedia).

    Winners are bullet lines under "== Liste des lauréats ==" shaped like:
        * [[2024 en littérature|2024]] : page 111 de ''Titre'' d'[[Auteur]] (Éditeur)
    """
    idx = wikitext.find('Liste des lauréats')
    body = wikitext[idx:] if idx >= 0 else wikitext
    books = []
    for line in body.splitlines():
        s = line.strip()
        # Only top-level winner bullets (skip "**" special-mention sub-bullets).
        if not s.startswith('*') or s.startswith('**'):
            continue
        ym = re.search(r'\[\[(\d{4})\b', s)
        if not ym:
            continue
        year = ym.group(1)
        # Drop the "page 111 de " / "page 111 d'" prefix first, so the French
        # elided apostrophe in "d'" cannot merge with the title's '' markers
        # (e.g. "d'''Achab''" would otherwise capture a stray leading quote).
        pm0 = re.search(r"page\s*111\s+d(?:e\b|['\u2019])\s*", s, re.I)
        rest = s[pm0.end():] if pm0 else s
        # Title is the first ''italicised'' work in the remainder.
        tm = re.search(r"''(.+?)''", rest)
        if not tm:
            continue
        title = _strip_wiki(tm.group(1))
        # Author sits between the title and the "(Publisher)": "de X" / "d'X".
        after = rest[tm.end():]
        am = re.search(r"\bd[e\u2019\']\s*(.+?)\s*\(", after, re.S)
        author = _strip_wiki(am.group(1)) if am else ''
        pm = re.search(r'\(([^)]+)\)', after)
        publisher = _strip_wiki(pm.group(1)) if pm else ''
        if not title:
            continue
        books.append({
            'rank': str(len(books) + 1),
            'title': title,
            'authors': author or '\u2014',
            'cover_url': '',
            'source_url': 'https://fr.wikipedia.org/wiki/Prix_de_la_page_111',
            'blurb': 'Prix de la page 111 ({}){}'.format(
                year, ' — ' + publisher if publisher else ''),
        })
    books.reverse()  # newest winner first
    for i, b in enumerate(books, 1):
        b['rank'] = str(i)
    return books


def _cell_text(line):
    """Turn a wikitext table-cell line into plain text.

    Handles attribute prefixes such as
    ``| data-sort-value="Aridjis, Chloe" | [[Chloe Aridjis]]`` by stripping
    the attribute block (which contains a pipe that is NOT a cell separator).
    """
    if not line.startswith('|'):
        line = '|' + line
    body = line[1:]
    m = re.match(r'\s*data-sort-value="[^"]*"\s*\|', body)
    if m:
        body = body[m.end():]
    return _strip_wiki(body)


def _parse_booker_2026(wikitext):
    """Parse the 2026 Booker Prize nominees table
    (Author | Title | Country | Publisher).

    Shortlisted rows carry ``|-style="background: lightgrey"`` and winner
    rows ``background: gold`` (none yet for 2026 — winner due 9 Nov 2026).
    Shortlisted books rank first, then the remaining longlist, each keeping
    the article's alphabetical order.
    """
    idx = wikitext.find('==Nominees')
    start = wikitext.find('{|', idx if idx >= 0 else 0)
    if start < 0:
        return []
    end = wikitext.find('|}', start)
    table = wikitext[start:end if end > 0 else len(wikitext)]

    shortlist, longlist = [], []
    for row in re.split(r'\n\|-', table):
        if row.lstrip().startswith('!') or row.strip() == '{|':
            continue
        is_short = 'background: lightgrey' in row.splitlines()[0] if row.splitlines() else False
        is_gold = 'background: gold' in row.splitlines()[0] if row.splitlines() else False
        cells = []
        for line in row.splitlines():
            s = line.strip()
            if s.startswith('|') and not s.startswith('|-') and not s.startswith('|+'):
                cells.append(_cell_text(s))
        if len(cells) < 4 or not cells[0]:
            continue
        author, title, country, publisher = (cells + ['', '', '', ''])[:4]
        if not title:
            continue
        entry = {
            'title': title,
            'authors': author,
            'cover_url': '',
            'source_url': 'https://en.wikipedia.org/wiki/2026_Booker_Prize',
            'blurb': '2026 Booker Prize \u2014 {} ({}{})'.format(
                'Winner' if is_gold else ('Shortlisted' if is_short else 'Longlisted'),
                publisher, ', ' + country if country else ''),
        }
        (shortlist if (is_short or is_gold) else longlist).append(entry)

    books = shortlist + longlist
    for i, b in enumerate(books, 1):
        b['rank'] = str(i)
    return books


PAGES_BASE = 'https://cm-fy.github.io/best-sellers-gr-cache'


def _reattach_covers(slug, books):
    """Re-link hosted covers after a re-parse so re-runs are non-destructive.

    The parsers emit empty cover_url values; any cover already hosted under
    gr/covers/<slug>/<rank>.jpg is re-linked by rank.
    """
    cover_dir = os.path.join(OUTPUT_DIR, 'covers', slug)
    if not os.path.isdir(cover_dir):
        return
    for b in books:
        rank = str(b.get('rank') or '').strip()
        if not rank:
            continue
        if os.path.exists(os.path.join(cover_dir, rank + '.jpg')):
            b['cover_url'] = '{}/gr/covers/{}/{}.jpg'.format(PAGES_BASE, slug, rank)
    return books


PARSERS = {
    'diagram_prize': _parse_diagram,
    'wodehouse': _parse_wodehouse,
    'prix_page_111': _parse_prix_page_111,
    'booker_2026': _parse_booker_2026,
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
            wt = _fetch_wikitext(session, title, _WIKI_API_FOR.get(slug, API))
            parser = PARSERS.get(slug)
            books = parser(wt) if parser else []
            _reattach_covers(slug, books)
            path = _write(slug, books)
            print('  {} books -> {}'.format(len(books), path))
        except Exception as e:  # noqa: BLE001
            print('  ERROR: {}'.format(e), file=sys.stderr)
            _write(slug, [])
        time.sleep(0.5)


if __name__ == '__main__':
    sys.exit(main())
