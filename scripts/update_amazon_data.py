#!/usr/bin/env python3
"""Refreshes data/amazon.json with the book's current Amazon.com figures: average rating, number of
ratings, star breakdown, the three prices and any new 5-star verified reviews. GitHub Actions runs it
every day (.github/workflows/amazon-data.yml). The figures come from Apify's Amazon Product Scraper
(junglee/amazon-crawler), with the APIFY_TOKEN repository secret.

Nothing is written when the secret is missing, when the request fails or when a value looks wrong:
the page then keeps showing the last good figures with their date. Reviews already in the file are
never changed; new 5-star verified ones from the United States are added on top, and past 9 reviews
only the 9 most recent are kept."""
import datetime
import json
import os
import re
import sys
import urllib.request
import zoneinfo

DATA = os.environ.get('AMAZON_DATA', 'data/amazon.json')
ENDPOINT = os.environ.get('APIFY_ENDPOINT', 'https://api.apify.com/v2/acts/junglee~amazon-crawler/run-sync-get-dataset-items?timeout=280')
ASINS = {'paperback': 'B0HH9YSD8H', 'hardcover': 'B0HJGJ82BP', 'kindle': 'B0HH4TXCX5'}
LABELS = {'paperback': 'Paperback', 'hardcover': 'Hardcover', 'kindle': 'Kindle'}
NAME_KEYS = ('author', 'username', 'userName', 'reviewerName', 'reviewer', 'profileName', 'name')
REFRESH_DAYS = 7   # rewrite the file at least this often, so the date on the page stays recent
MAX_REVIEWS = 9    # the page shows at most 9


def stop(message):
    print(f'::warning::{message}')
    sys.exit(0)


def asin_of(item):
    a = str(item.get('asin') or '').strip().upper()
    if re.fullmatch(r'[A-Z0-9]{10}', a):
        return a
    m = re.search(r'/dp/([A-Z0-9]{10})', str(item.get('url') or ''))
    return m.group(1) if m else ''


def money(price):
    v = price.get('value') if isinstance(price, dict) else price
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return f'${v:.2f}' if 0 < v < 10000 else None


def breakdown(raw):
    if not isinstance(raw, dict):
        return None
    vals = {}
    for k in '54321':
        v = raw.get(f'{k}star', raw.get(k))
        try:
            vals[k] = float(v)
        except (TypeError, ValueError):
            return None
    if all(v <= 1 for v in vals.values()):          # shares (0.88) rather than percentages (88)
        vals = {k: v * 100 for k, v in vals.items()}
    hist = {k: int(round(v)) for k, v in vals.items()}
    if not all(0 <= v <= 100 for v in hist.values()) or not 95 <= sum(hist.values()) <= 105:
        return None
    return hist


def clean(v):
    return v.strip() if isinstance(v, str) else ''


def review_name(r):
    for k in NAME_KEYS:
        v = r.get(k)
        if isinstance(v, dict):
            v = v.get('name')
        if clean(v):
            return clean(v)
    return ''


def review_date(r):
    d = clean(r.get('date'))
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', d[:10]):
        return d[:10]
    m = re.search(r'([A-Z][a-z]+ \d{1,2}, \d{4})', f"{clean(r.get('reviewedIn'))} {d}")   # "... on October 7, 2026"
    if m:
        try:
            return datetime.datetime.strptime(m.group(1), '%B %d, %Y').date().isoformat()
        except ValueError:
            pass
    return ''


def review_format(r, page_asin):
    variant = clean(r.get('variant') or r.get('format')).lower()
    for label in LABELS.values():
        if label.lower() in variant:
            return label
    asin = clean(r.get('productAsin')).upper() or page_asin
    return next((LABELS[f] for f, a in ASINS.items() if a == asin), '')


def reviews_from(items):
    """5-star verified reviews from the United States shown on the book's Amazon pages, as the page needs them."""
    found, fields = [], set()
    for item in items:
        for r in item.get('productPageReviews') or []:
            if not isinstance(r, dict):
                continue
            fields.update(r.keys())
            try:
                stars = float(r.get('ratingScore', r.get('rating', r.get('stars'))))
            except (TypeError, ValueError):
                continue
            where = clean(r.get('reviewedIn'))
            if stars != 5 or r.get('isVerified', r.get('verified')) is not True or (where and 'United States' not in where):
                continue
            title = re.sub(r'^\d(\.\d)? out of 5 stars\s*', '', clean(r.get('reviewTitle') or r.get('title')))
            text = re.sub(r'\s*Read more$', '', clean(r.get('reviewDescription') or r.get('text')))
            rev = {'name': review_name(r), 'stars': 5, 'verified': True, 'format': review_format(r, asin_of(item)),
                   'date': review_date(r), 'title': title, 'text': text}
            if rev['name'] and rev['date'] and rev['text']:
                found.append(rev)
    return found, fields


def merge_reviews(current, found):
    """New reviews go on top, newest first; past 9 reviews, the 9 most recent are kept."""
    have = {(r.get('name'), r.get('date')) for r in current if isinstance(r, dict)}
    fresh = []
    for r in sorted(found, key=lambda r: r['date'], reverse=True):
        if (r['name'], r['date']) not in have:
            have.add((r['name'], r['date']))
            fresh.append(r)
    merged = fresh + current
    if len(merged) > MAX_REVIEWS:
        merged = sorted(merged, key=lambda r: str(r.get('date', '')), reverse=True)[:MAX_REVIEWS]
    return merged, [r for r in fresh if r in merged]


def main():
    token = os.environ.get('APIFY_TOKEN', '').strip()
    if not token:
        stop('The APIFY_TOKEN secret is not set yet: nothing updated.')
    current = json.load(open(DATA, encoding='utf-8'))

    payload = {
        'categoryOrProductUrls': [{'url': f'https://www.amazon.com/dp/{a}'} for a in ASINS.values()],
        'maxItemsPerStartUrl': 1,
        'maxProductVariantsAsSeparateResults': 0,
        'maxOffers': 0,
        'proxyCountry': 'AUTO_SELECT_PROXY_COUNTRY',
    }
    req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), method='POST',
                                 headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=320) as res:
            items = json.load(res)
    except Exception as e:  # network error, Apify error, quota used up...
        stop(f'Apify request failed ({type(e).__name__}): nothing updated.')
    if not isinstance(items, list):
        stop('Unexpected answer from Apify: nothing updated.')
    items = [i for i in items if isinstance(i, dict)]
    by_asin = {asin_of(i): i for i in items if asin_of(i)}

    book = by_asin.get(ASINS['paperback'])
    if not book:
        stop('No data for the paperback in the answer: nothing updated.')
    try:
        rating = round(float(book.get('stars')), 1)
        count = int(str(book.get('reviewsCount')).replace(',', ''))
    except (TypeError, ValueError):
        stop('Rating or number of ratings missing: nothing updated.')
    if not 1 <= rating <= 5 or count < 0:
        stop(f'Implausible rating ({rating}) or number of ratings ({count}): nothing updated.')

    prices = dict(current.get('prices') or {})
    for fmt, asin in ASINS.items():
        p = money((by_asin.get(asin) or {}).get('price'))
        if p:                       # a missing or $0.00 (Kindle Unlimited) price keeps the last good one
            prices[fmt] = p

    hist = breakdown(book.get('starsBreakdown'))     # None hides the breakdown rather than showing old numbers

    old_reviews = [r for r in current.get('reviews') or [] if isinstance(r, dict)]
    found, fields = reviews_from(items)
    reviews, added = merge_reviews(old_reviews, found)
    if fields:
        print(f"Review fields from Apify: {', '.join(sorted(fields))}")
    print(f'Reviews: {len(found)} five-star verified on the Amazon pages today, {len(added)} new.')

    today = datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Rome')).date()
    new = dict(current)
    new.update(asOf=today.isoformat(), rating=rating, ratingsCount=count, histogram=hist, prices=prices, reviews=reviews)

    unchanged = all(new.get(k) == current.get(k) for k in ('rating', 'ratingsCount', 'histogram', 'prices', 'reviews'))
    try:
        age = (today - datetime.date.fromisoformat(current.get('asOf', ''))).days
    except ValueError:
        age = REFRESH_DAYS
    summary = f"{rating} out of 5, {count} ratings, breakdown {hist}, prices {prices}"
    if unchanged and age < REFRESH_DAYS:
        print(f'No change ({summary}); last written {age} day(s) ago.')
        return
    with open(DATA, 'w', encoding='utf-8') as f:
        f.write(json.dumps(new, ensure_ascii=False, indent=2) + '\n')
    for r in added:
        print(f"New review: {r['name']}, {r['date']}, {r['format'] or 'format not shown'}: {r['title']}")
    print(f'Updated {DATA}: {summary}.')


if __name__ == '__main__':
    main()
