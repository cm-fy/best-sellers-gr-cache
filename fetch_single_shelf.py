#!/usr/bin/env python3
"""Fetch a single shelf for testing."""
import json
import sys
from fetch_goodreads import _session, parse_goodreads, _write

SHELF = 'young-adult'
URL = f'https://www.goodreads.com/shelf/show/{SHELF}'

print(f'Fetching shelf {SHELF}: {URL}')
session = _session()

import requests
r = session.get(URL, timeout=30)
print(f'  Status: {r.status_code}')

html = r.text
books = parse_goodreads(html, session)
print(f'  {len(books)} books parsed')

# Quick check
if books:
    first = books[0]
    print(f'\nFirst book: {first["title"]}')
    print(f'  Blurb: {first.get("blurb", "")[:100]}...')

_write(f'shelf_{SHELF}', books)
print(f'\nWrote gr/shelf_{SHELF}.json')
