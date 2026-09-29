#!/usr/bin/env python3
"""Test the book ID matching logic."""
import re
import requests
from urllib.parse import quote

GR_AC_URL = 'https://www.goodreads.com/book/auto_complete?format=json&q='

def _extract_book_id(url):
    m = re.search(r'/book/show/(\d+)', url)
    return m.group(1) if m else None

# Test data
test_cases = [
    {
        'title': 'The Hunger Games',
        'author': 'Suzanne Collins',
        'url': 'https://www.goodreads.com/book/show/2767052-the-hunger-games',
        'expected_book_id': '2767052'
    }
]

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
}

for test in test_cases:
    print(f"\nTesting: {test['title']}")
    print(f"URL: {test['url']}")

    # Extract book ID
    book_id = _extract_book_id(test['url'])
    print(f"Extracted book_id: {book_id}")
    print(f"Expected book_id: {test['expected_book_id']}")
    print(f"Match: {book_id == test['expected_book_id']}")

    # Query autocomplete
    query = quote((test['title'] + ' ' + test['author']).strip().encode('utf-8'))
    url = GR_AC_URL + query
    print(f"\nQuery URL: {url[:80]}...")

    try:
        r = requests.get(url, headers=headers, timeout=15)
        results = r.json()
        print(f"Got {len(results)} results")

        # Check first result
        if results:
            first = results[0]
            print(f"\nFirst result:")
            print(f"  bookId: {first.get('bookId')}")
            print(f"  title: {first.get('title')}")
            desc = first.get('description', {})
            if isinstance(desc, dict):
                blurb = desc.get('html', '')[:100]
                print(f"  blurb: {blurb}...")

            # Check if we find a match
            hit = None
            for result in results:
                if result.get('bookId') == str(book_id):
                    hit = result
                    break

            if hit:
                print(f"\nMATCH FOUND at result with bookId={hit.get('bookId')}")
                desc = hit.get('description', {})
                if isinstance(desc, dict):
                    blurb = desc.get('html', '')[:100]
                    print(f"  Correct blurb: {blurb}...")
            else:
                print(f"\nNO MATCH - using first result (wrong blurb)")

    except Exception as e:
        print(f"Error: {e}")
