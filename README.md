# 100,000 Whys for Kids: landing page

- `index.html`: the landing page. Visitors leave first name, email and age to see it and get the free Myth-Buster Cards (under-18s can't sign up or enter)
- `privacy.html`: privacy policy
- `data/amazon.json`: rating, number of ratings, rating breakdown, prices and the 5-star reviews shown on the page, as taken from Amazon.com on the `asOf` date. The page reads it on every visit.
- `.github/workflows/amazon-data.yml` and `scripts/update_amazon_data.py`: every day GitHub refreshes `data/amazon.json` from Amazon.com through Apify (repository secret `APIFY_TOKEN`). Actions → Update Amazon figures → Run workflow updates it at once.

Settings (Google Form, Meta Pixel ID, privacy link) are in `window.LEADS` near the top of each page.
Built from the working files with `kit/build_site.py`. Public address: https://100000whyskids.com/
