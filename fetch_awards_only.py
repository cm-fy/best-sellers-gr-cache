#!/usr/bin/env python3
"""Scoped runner: scrape ONLY the AWARD_LISTS into gr/award_<slug>.json.

Reuses fetch_goodreads.py internals so the parsing/enrichment logic stays in one
place. Skips the (slow) shelves and best-of lists.
"""
import sys

import fetch_goodreads as fg


def main():
    import os
    os.makedirs(fg.OUTPUT_DIR, exist_ok=True)
    session = fg._session()
    ok = 0
    for slug, label, award_id in fg.AWARD_LISTS:
        url = fg._award_url(award_id)
        out_slug = 'award_' + slug
        print('Fetching {}: {}'.format(label, url))
        try:
            html, is_captcha = fg._fetch(session, url)
            if is_captcha:
                print('  CAPTCHA — skipping')
                fg._write(out_slug, [])
                continue
            books = fg.parse_goodreads(html, session)
            fg._write(out_slug, books)
            print('  {} books'.format(len(books)))
            if books:
                ok += 1
        except Exception as e:  # noqa: BLE001
            print('  ERROR: {}'.format(e), file=sys.stderr)
            fg._write(out_slug, [])
    print('Done. {} / {} awards populated.'.format(ok, len(fg.AWARD_LISTS)))


if __name__ == '__main__':
    sys.exit(main())
