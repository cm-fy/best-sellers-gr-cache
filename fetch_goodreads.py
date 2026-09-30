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
import time
from datetime import datetime
from urllib.parse import urljoin, quote

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

# Goodreads autocomplete API for fetching additional book metadata
GR_AC_URL = 'https://www.goodreads.com/book/auto_complete?format=json&q='

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

# ── Award lists ─────────────────────────────────────────────────────────────
# Goodreads /award/show/<id> pages are plain "bookTitle" list tables, so the
# same parser handles them.  IDs below were verified by scanning the live
# award index (see Best-Sellers/scratch/award_id_scan.py).  The slug in the URL
# is cosmetic — Goodreads routes purely on the numeric ID — so we build URLs
# from the ID and a human-readable slug for the cached filename.
#
# (slug, label, goodreads_award_id).  Output file: gr/award_<slug>.json
AWARD_LISTS = [
    # -- International / non-English ------------------------------------------
    ('litt_policiere',      'Grand Prix de Littérature Policière (France)', 195),
    ('premio_nadal',        'Premio Nadal (Spain)',                         545),
    ('premio_herralde',     'Premio Herralde de Novela (Spain)',            537),
    ('romulo_gallegos',     'Premio Rómulo Gallegos (Venezuela)',           538),
    ('kurd_lasswitz',       'Kurd-Laßwitz-Preis (Germany, SF)',             210),
    ('buxtehuder_bulle',    'Buxtehuder Bulle (Germany, YA)',               295),
    ('internat_literaturpreis', 'Internationaler Literaturpreis (Germany)', 495),
    ('nederlandse_boek',    'Publieksprijs Nederlandse Boek (Netherlands)', 457),
    ('golshiri',            'Houshang Golshiri Award (Iran)',               733),
    ('frank_oconnor',       'Frank O’Connor Short Story Award (Ireland)',   707),
    # -- UK & Commonwealth ----------------------------------------------------
    ('booker_prize',        'Booker Prize',                                  13),
    ('womens_prize',        'Women’s Prize for Fiction (Orange)',            90),
    ('costa_whitbread',     'Costa / Whitbread Book Award',                 111),
    ('james_tait_black',    'James Tait Black Memorial Prize',               94),
    ('somerset_maugham',    'Somerset Maugham Award',                       278),
    ('guardian_first_book', 'Guardian First Book Award',                     92),
    ('guardian_fiction',    'Guardian Fiction Award',                        89),
    ('llewellyn_rhys',      'John Llewellyn Rhys Prize',                    246),
    ('wh_smith_literary',   'WH Smith Literary Award',                      480),
    ('kate_greenaway',      'Kate Greenaway Medal',                         690),
    ('winifred_holtby',     'Winifred Holtby Memorial Prize',              704),
    ('warwick_prize',       'Warwick Prize for Writing',                     87),
    ('arthur_c_clarke',     'Arthur C. Clarke Award (UK, SF)',               76),
    ('bsfa',                'BSFA Award (UK, SF)',                          243),
    ('gemmell_legend',      'David Gemmell Legend Award (UK, fantasy)',     550),
    ('cwa_silver_dagger',   'CWA Silver Dagger (UK, crime)',                535),
    ('stephen_leacock',     'Stephen Leacock Medal (Canada, humour)',      416),
    ('sunburst',            'Sunburst Award (Canada, SF/F)',                475),
    ('geffen',              'Geffen Award (Israel, SF/F)',                  106),
    # -- North America --------------------------------------------------------
    ('pulitzer',            'Pulitzer Prize',                                16),
    ('national_book',       'National Book Award',                           33),
    # -- Non-Fiction & Science ------------------------------------------------
    ('royal_society_science', 'Royal Society Science Book Prize',            11),
    ('goldsmith',           'Goldsmith Book Prize',                         755),
    # -- Speculative Fiction (additions) -------------------------------------
    ('nebula',              'Nebula Award (US, SF/F)',                       23),
    ('world_fantasy',       'World Fantasy Award',                          100),
    ('bram_stoker',         'Bram Stoker Award (horror)',                     7),
    ('locus',               'Locus Award (SF/F)',                            46),
    # -- Crime & Mystery ------------------------------------------------------
    ('anthony',             'Anthony Award (US, crime)',                    145),
    ('barry',               'Barry Award (US, crime)',                       54),
    ('hammett',             'Hammett Prize (North America, crime)',         365),
    ('shamus',              'Shamus Award (US, PI fiction)',                585),
    # -- Anti-awards & oddities ----------------------------------------------
    ('bad_sex_fiction',     'Bad Sex in Fiction Award',                   12341),
    ('coogler',             'J. Gordon Coogler Award (worst book)',          40),
    ('thurber_prize',       'Thurber Prize for American Humor',           32007),
    ('not_the_booker',      'Guardian Not the Booker Prize',              15615),
]


def _award_url(award_id):
    return 'https://www.goodreads.com/award/show/{}'.format(award_id)

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


def _extract_book_id(url):
    """Extract the numeric book ID from a Goodreads book URL.

    Examples:
        /book/show/23692271-sapiens → 23692271
        https://www.goodreads.com/book/show/23692271-sapiens → 23692271
    """
    m = re.search(r'/book/show/(\d+)', url)
    return m.group(1) if m else None


def _parse_table_rows(html, session=None):
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
        authors_str = ', '.join(a.strip() for a in authors if a.strip()) or '\u2014'
        cover_url = ''
        cm = re.search(r'<img[^>]+src="(https?://[^"]+)"', row, re.I)
        if cm:
            cover_url = re.sub(r'\._S[XY]\d+_', '._SY200_', cm.group(1))

        book = {
            'rank':       str(len(books) + 1),
            'title':      title,
            'authors':    authors_str,
            'cover_url':  cover_url,
            'source_url': src_url,
        }

        # Fetch additional metadata from autocomplete API
        # Use book_id to ensure we get the correct book's blurb, not a summary/guide
        if session:
            book_id = _extract_book_id(src_url)
            ac_data = _gr_autocomplete(session, title,
                                        authors_str if authors_str != '\u2014' else '',
                                        book_id=book_id)
            if ac_data:
                book['blurb'] = ac_data.get('blurb', '')
                book['rating'] = ac_data.get('rating')
                book['votes'] = ac_data.get('rating_count')
                book['pages'] = ac_data.get('num_pages')
                book['format'] = ac_data.get('format', '')
                # Use author from autocomplete if HTML parsing failed
                if authors_str == '\u2014' and ac_data.get('author_name'):
                    book['authors'] = ac_data['author_name']

        books.append(book)
    return books


def _parse_ranked_headings(html, session=None):
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
        authors_str = ', '.join(authors) or '\u2014'
        cover_url = ''
        cm = re.search(r'<img[^>]+src="(https?://[^"]+)"', row, re.I)
        if cm:
            cover_url = re.sub(r'\._S[XY]\d+_', '._SY200_', cm.group(1))

        book_entry = {
            'rank':       m.group(1),
            'title':      title,
            'authors':    authors_str,
            'cover_url':  cover_url,
            'source_url': src_url,
        }

        # Fetch additional metadata from autocomplete API
        # Use book_id to ensure we get the correct book's blurb, not a summary/guide
        if session:
            book_id = _extract_book_id(src_url)
            ac_data = _gr_autocomplete(session, title,
                                        authors_str if authors_str != '\u2014' else '',
                                        book_id=book_id)
            if ac_data:
                book_entry['blurb'] = ac_data.get('blurb', '')
                book_entry['rating'] = ac_data.get('rating')
                book_entry['votes'] = ac_data.get('rating_count')
                book_entry['pages'] = ac_data.get('num_pages')
                book_entry['format'] = ac_data.get('format', '')
                # Use author from autocomplete if HTML parsing failed
                if authors_str == '\u2014' and ac_data.get('author_name'):
                    book_entry['authors'] = ac_data['author_name']

        books.append(book_entry)
        if len(books) >= 50:
            break
    return books


def _parse_shelf_cards(html, session=None):
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

        authors_str = ', '.join(authors) or '\u2014'

        book_entry = {
            'rank':       str(len(books) + 1),
            'title':      title,
            'authors':    authors_str,
            'cover_url':  cover_url,
            'source_url': src_url,
        }

        # Fetch additional metadata from autocomplete API
        # Use book_id to ensure we get the correct book's blurb, not a summary/guide
        if session:
            book_id = _extract_book_id(src_url)
            ac_data = _gr_autocomplete(session, title,
                                        authors_str if authors_str != '\u2014' else '',
                                        book_id=book_id)
            if ac_data:
                book_entry['blurb'] = ac_data.get('blurb', '')
                book_entry['rating'] = ac_data.get('rating')
                book_entry['votes'] = ac_data.get('rating_count')
                book_entry['pages'] = ac_data.get('num_pages')
                book_entry['format'] = ac_data.get('format', '')
                # Use author from autocomplete if HTML parsing failed
                if authors_str == '\u2014' and ac_data.get('author_name'):
                    book_entry['authors'] = ac_data['author_name']

        books.append(book_entry)
        if len(books) >= 50:
            break

    return books


# Rate limiting for autocomplete API calls
_ac_last_call = 0
_ac_min_interval = 0.5  # seconds between calls

# ── Derivative-work detection ─────────────────────────────────────────────────
# Phrases that indicate a result is NOT the original book but a summary, study
# guide, digest, companion, "articles about", sidekick, review, analysis, etc.
_DERIVATIVE_TITLE_PHRASES = (
    'summary', 'study guide', 'study companion', 'companion to',
    'sidekick', 'analysis', 'digest', 'quick student workbook',
    'teacher guide', 'teaching guide', 'lesson plans',
    'review and analysis', 'summary and analysis', 'summary & analysis',
    'book review', 'review summary', 'bookmarked',
    'articles on', 'articles about', 'wikipedia',
    'independent companion', 'independent publication',
    'not written by', 'this is not', 'disclaimer',
    'sparknotes', 'cliff notes', 'cliffsnotes', 'bookrags',
    'by summary', 'a summary of', 'summary of',
    'concise new guide', 'concise and insightful',
    'unlock the more straightforward side',
    'finish in one sitting',
    'grab this', 'want to read but don',
    'book analysis', 'key summary breakdown',
    '30-minute study guide', 'booknotes', 'expert book reviews',
    'the big read', 'resources to integrate',
    'detailed summary', 'quark notes', 'abookaday',
    'instanalysis', 'bright summaries', 'one sitting publications',
)

_DERIVATIVE_AUTHORS = (
    'hephaestus books', 'bookrags', 'bookbuddy', 'bookmarked',
    'sparknotes', 'cliffs notes', 'cliffsnotes',
    'riyan zia', 'susan brown summary', 'daily books',
    'summary station', 'summary world', 'reads summaries',
    'instaread', 'worth books', 'swift reads',
    'bright summaries', 'abookaday', 'instanalysis',
    'expert book reviews', 'booknotes', 'quark notes',
    'one sitting publications', 'katherine r. miller',
    'harold hanson', 'anne twomey', 'marsha james',
)


def _is_derivative_work(hit):
    """Return True if an autocomplete result is a summary/study guide/digest
    rather than the original book."""
    if not isinstance(hit, dict):
        return False
    title = (hit.get('title') or '').lower()
    for phrase in _DERIVATIVE_TITLE_PHRASES:
        if phrase in title:
            return True
    author_name = ''
    author_obj = hit.get('author')
    if isinstance(author_obj, dict):
        author_name = (author_obj.get('name') or '').lower()
    elif isinstance(author_obj, str):
        author_name = author_obj.lower()
    if author_name:
        for der in _DERIVATIVE_AUTHORS:
            if der in author_name:
                return True
    desc_obj = hit.get('description') or {}
    blurb_html = desc_obj.get('html', '') if isinstance(desc_obj, dict) else ''
    if blurb_html:
        blurb_lower = re.sub(r'<[^>]+>', ' ', blurb_html).lower()
        for phrase in ('this book does not contain',
                       'this is not written by',
                       'this is an independent',
                       'this study guide',
                       'consists of public domain articles'):
            if phrase in blurb_lower:
                return True
    return False


def _gr_normalize(text):
    """Normalize text for comparison (casefold, strip punctuation)."""
    import unicodedata
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r'[^\w\s]', ' ', text, flags=re.U)
    return re.sub(r'\s+', ' ', text).strip()


def _gr_title_similarity(list_title, result_title):
    """Score how well a result title matches the list title (0.0 - 1.0)."""
    lt = _gr_normalize(list_title)
    rt = _gr_normalize(result_title)
    if not lt or not rt:
        return 0.0
    if lt == rt:
        return 1.0
    lt_words = [w for w in lt.split() if w]
    rt_words = [w for w in rt.split() if w]
    if not lt_words or not rt_words:
        return 0.0
    lt_set, rt_set = set(lt_words), set(rt_words)
    common = lt_set.intersection(rt_set)
    if not common:
        return 0.0
    containment = len(common) / len(lt_set)
    extra = max(0, len(rt_set) - len(lt_set)) / max(len(rt_set), 1)
    score = containment * (1.0 - 0.3 * extra)
    if rt.startswith(lt) or lt.startswith(rt):
        score = min(1.0, score + 0.15)
    return max(0.0, min(1.0, score))


def _gr_author_keys(authors):
    """Extract normalized author match keys."""
    if isinstance(authors, (list, tuple)):
        parts = [str(a) for a in authors]
    else:
        parts = re.split(r'\s*(?:,|;|\band\b|&|\+)\s*', authors or '', flags=re.I)
    keys = []
    for part in parts:
        cleaned = re.sub(r'^\s*by\s+', '', part, flags=re.I)
        key = _gr_normalize(cleaned)
        if not key or key == _gr_normalize('\u2014'):
            continue
        if key not in keys:
            keys.append(key)
        try:
            last = key.split()[-1]
        except Exception:
            last = ''
        if last and last not in keys:
            keys.append(last)
    return keys


def _gr_hit_author(hit):
    """Extract author name string from an autocomplete hit."""
    ao = hit.get('author')
    if isinstance(ao, dict):
        return ao.get('name', '')
    if isinstance(ao, str):
        return ao
    return ''


def _gr_pick_best_hit(results, title, author='', book_id=None):
    """Pick the best autocomplete result, avoiding derivative works.

    Tiers:
      1. Exact bookId match (definitive).
      2. Best title-similarity among non-derivative, author-matching results.
      3. Best title-similarity among non-derivative results.
      4. First non-derivative result.
      5. None if all derivative + have book_id; else results[0] (backward compat).
    """
    if not results:
        return None

    # Tier 1: exact bookId match
    if book_id:
        bid = str(book_id)
        for r in results:
            if str(r.get('bookId', '')) == bid:
                return r

    have_author = bool(author and author.strip() and author.strip() != '\u2014')
    auth_keys = set()
    if have_author:
        for k in _gr_author_keys(author):
            if k and k != _gr_normalize('\u2014'):
                auth_keys.add(k)

    scored = []
    for r in results:
        if _is_derivative_work(r):
            continue
        sim = _gr_title_similarity(title, r.get('title', ''))
        r_auth = _gr_hit_author(r)
        r_auth_keys = set(_gr_author_keys(r_auth)) if r_auth else set()
        author_match = bool(auth_keys and r_auth_keys and auth_keys.intersection(r_auth_keys))
        scored.append((sim, author_match, r))

    if scored:
        author_matched = [s for s in scored if s[1]]
        if author_matched:
            author_matched.sort(key=lambda s: s[0], reverse=True)
            return author_matched[0][2]
        scored.sort(key=lambda s: s[0], reverse=True)
        return scored[0][2]

    # Tier 4: first non-derivative result
    for r in results:
        if not _is_derivative_work(r):
            return r

    # Tier 5: all derivative
    if book_id:
        return None
    return results[0]


def _gr_fetch_book_page(session, book_id):
    """Fetch the actual Goodreads book page and extract authoritative metadata.

    This is the definitive source — no matching ambiguity, no derivative works,
    no wrong series numbers.  The book page embeds a __NEXT_DATA__ JSON blob
    containing an Apollo cache with Book: and Work: objects.

    Returns a dict with blurb, rating, rating_count, num_pages, book_id,
    author_name, format, asin, publisher, title_complete — or None.
    """
    if not book_id:
        return None
    try:
        url = f'https://www.goodreads.com/book/show/{book_id}'
        r = session.get(url, timeout=20)
        r.raise_for_status()
        html = r.text
        jm = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                       html, re.DOTALL | re.I)
        if not jm:
            return None
        data = json.loads(jm.group(1))
        apollo = data.get('props', {}).get('pageProps', {}).get('apolloState', {})
        if not apollo:
            return None

        # Find the full Book: object (there may be stubs for other editions)
        book_obj = None
        for key in apollo:
            if key.startswith('Book:'):
                candidate = apollo[key]
                if not isinstance(candidate, dict):
                    continue
                if candidate.get('description') or candidate.get('title'):
                    book_obj = candidate
                    break
        if not book_obj:
            for key in apollo:
                if key.startswith('Book:'):
                    book_obj = apollo[key]
                    break
        if not book_obj:
            return None

        # Find the Work: object (for stats)
        work_obj = None
        work_ref = book_obj.get('work', {})
        if isinstance(work_ref, dict) and '__ref' in work_ref:
            work_obj = apollo.get(work_ref['__ref'])
        if not work_obj:
            for key in apollo:
                if key.startswith('Work:'):
                    work_obj = apollo[key]
                    break

        # Blurb
        blurb = ''
        desc = book_obj.get('description')
        if isinstance(desc, str):
            blurb = re.sub(r'<[^>]+>', ' ', desc)
            blurb = _decode_html(re.sub(r'\s+', ' ', blurb).strip())
        elif isinstance(desc, dict):
            blurb_html = desc.get('html', '') or desc.get('text', '')
            blurb = re.sub(r'<[^>]+>', ' ', blurb_html)
            blurb = _decode_html(re.sub(r'\s+', ' ', blurb).strip())

        # Details
        details = book_obj.get('details', {})
        if not isinstance(details, dict):
            details = {}
        book_format = details.get('format', '') or ''
        num_pages = details.get('numPages') or 0
        asin = details.get('asin', '') or ''
        publisher = details.get('publisher', '') or ''

        # Rating from Work stats
        rating = None
        rating_count = None
        if work_obj:
            stats = work_obj.get('stats', {})
            if isinstance(stats, dict):
                rating = stats.get('averageRating')
                rating_count = stats.get('ratingsCount')

        # Cover URL
        cover_url = book_obj.get('imageUrl', '') or ''
        if cover_url:
            cover_url = re.sub(r'\._S[XY]\d+_', '._SY200_', cover_url)

        # Title
        title = book_obj.get('titleComplete') or book_obj.get('title', '') or ''

        # Author
        author_name = ''
        contrib = book_obj.get('primaryContributorEdge', {})
        if isinstance(contrib, dict):
            node = contrib.get('node', {})
            if isinstance(node, dict) and '__ref' in node:
                author_obj = apollo.get(node['__ref'], {})
                if isinstance(author_obj, dict):
                    author_name = author_obj.get('name', '') or ''

        return {
            'blurb':           blurb[:400] + ('\u2026' if len(blurb) > 400 else ''),
            'rating':          rating,
            'rating_count':    rating_count,
            'num_pages':       num_pages or None,
            'book_id':         str(book_obj.get('legacyId', book_id)),
            'author_name':     author_name,
            'format':          book_format,
            'asin':            asin,
            'publisher':       publisher,
            'title_complete':  title,
            'cover_url':       cover_url,
            'blurb_source':    'goodreads_book_page',
        }
    except Exception as e:
        print(f'    Book page error for {book_id}: {e}')
        return None


def _gr_autocomplete(session, title, author='', book_id=None):
    """Fetch additional book metadata from Goodreads.

    When book_id is available, fetches the actual book page (authoritative —
    no matching ambiguity, no derivative works).  Falls back to the autocomplete
    API when no book_id or when the page fetch fails.

    Returns dict with: blurb, rating, rating_count, num_pages, book_id,
    author_name, format, blurb_source — or None.
    """
    # ── Tier 0: fetch the actual book page (authoritative) ───────────────
    if book_id:
        page_data = _gr_fetch_book_page(session, book_id)
        if page_data and page_data.get('blurb'):
            return page_data

    # ── Fallback: autocomplete API ───────────────────────────────────────
    global _ac_last_call
    try:
        # Rate limiting
        now = time.time()
        elapsed = now - _ac_last_call
        if elapsed < _ac_min_interval:
            time.sleep(_ac_min_interval - elapsed)
        _ac_last_call = time.time()

        query = quote(title.strip().encode('utf-8'))
        url = GR_AC_URL + query
        r = session.get(url, timeout=15)
        r.raise_for_status()
        results = r.json()
        if not results:
            return None

        hit = _gr_pick_best_hit(results, title, author, book_id)
        if not hit:
            # All results were derivative works and we have a known book_id —
            # return None so the cache entry keeps its HTML-parsed data rather
            # than being polluted with a summary/study guide's wrong metadata.
            return None

        desc_obj = hit.get('description') or {}
        blurb_html = desc_obj.get('html', '') if isinstance(desc_obj, dict) else ''
        blurb = re.sub(r'<[^>]+>', ' ', blurb_html)
        blurb = _decode_html(re.sub(r'\s+', ' ', blurb).strip())

        # Extract author name from the autocomplete result
        author_obj = hit.get('author') or {}
        author_name = author_obj.get('name', '') if isinstance(author_obj, dict) else ''

        return {
            'blurb':        blurb[:400] + ('\u2026' if len(blurb) > 400 else ''),
            'rating':       hit.get('avgRating'),
            'rating_count': hit.get('ratingsCount'),
            'num_pages':    hit.get('numPages'),
            'book_id':      hit.get('bookId', ''),
            'author_name':  author_name,
            'format':       '',
            'blurb_source': 'goodreads_autocomplete',
        }
    except Exception as e:
        print(f'    Autocomplete error for "{title}": {e}')
        return None


def parse_goodreads(html, session=None):
    """Parse Goodreads HTML using the same multi-strategy approach as the plugin.

    If session is provided, fetches additional metadata (blurb, rating, votes, pages)
    from the autocomplete API for each book.
    """
    # Strategy 1: table rows with bookTitle class
    result = _parse_table_rows(html, session)
    if result:
        return result

    # Strategy 2: ranked headings (#1, #2, ...)
    result = _parse_ranked_headings(html, session)
    if result:
        return result

    # Strategy 3: card/shelf layout (BeautifulSoup)
    result = _parse_shelf_cards(html, session)
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

    all_lists = STATIC_LISTS + DYNAMIC_LISTS + [
        ('award_' + slug, label, _award_url(award_id))
        for slug, label, award_id in AWARD_LISTS
    ]
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
            books = parse_goodreads(html, session)
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
            books = parse_goodreads(html, session)
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
