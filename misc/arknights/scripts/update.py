"""Update the Arknights operator table: export the Cargo table from prts.wiki,
unescape it and write ../characters.csv.

prts.wiki is protected by an Anubis proof-of-work challenge, so requests and
curl only receive the challenge page. Playwright runs a real browser through it
(usually 1-3 seconds) and captures the download.

With a locally downloaded raw CSV (for example one exported through
fetch.http), use --from to skip fetching and only unescape:

    python update.py                    # fetch + unescape
    python update.py --from results.csv # unescape only
"""

import argparse
import html
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

CSV_PATH = Path(__file__).resolve().parent.parent / 'characters.csv'
# Download name chosen by the server; used for the temporary file
RAW_NAME = 'results.csv'
# Cargo export parameters; in fields, "field=column" makes the column header
EXPORT_PARAMS = {
    'title': 'Special:CargoExport',
    'tables': 'chara,chara_data,chara_extra_info,char_obtain',
    'join_on': ('chara._pageName=chara_data._pageName,'
                'chara._pageName=chara_extra_info._pageName,'
                'chara._pageName=char_obtain._pageName'),
    'fields': ('chara._pageName=干员,chara.profession=职业,chara.rarity=稀有度,'
               'chara.logo=标志,chara_extra_info.birthPlace=出身地,'
               'chara.team=团队,chara_extra_info.race=种族,chara.en=干员西文名,'
               'chara.jp=干员日文名,chara.eid=情报编号,chara_extra_info.sex=性别,'
               'chara.position=位置,chara.tag=标签,chara.subProfession=子职业,'
               'chara.nation=国家,chara.org=组织'),
    'where': 'chara.charIndex>0',
    'order_by': 'chara.charIndex ASC',
    'limit': '5000',
    'offset': '0',
    'format': 'csv',
}
EXPORT_URL = 'https://prts.wiki/index.php?' + urllib.parse.urlencode(EXPORT_PARAMS)
HOME_URL = 'https://prts.wiki/'
# User agent of a regular desktop Chrome; Anubis rejects a HeadlessChrome UA
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36')
# Host of the Anubis challenge page (sisyphus.prts.wiki)
CHALLENGE_HOST = 'sisyphus.'
# Challenge polling: 60 rounds, 0.5 s apart
POLL_TIMES = 60
POLL_INTERVAL = 0.5
# HTML entity pattern, used to detect leftovers after unescaping
ENTITY_PATTERN = re.compile(r'&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);')


def wait_for_challenge(page: Page) -> None:
    """Wait for the Anubis proof-of-work to finish and redirect back to the site."""
    for _ in range(POLL_TIMES):
        try:
            title = page.title()
        except PlaywrightError:
            title = ''  # title is unavailable during the redirect; retry next round
        if CHALLENGE_HOST not in page.url and 'bot' not in title.lower():
            return
        page.wait_for_timeout(POLL_INTERVAL * 1000)
    raise RuntimeError('Anubis challenge did not complete within 30 seconds')


def fetch_raw() -> bytes:
    """Open a browser, pass the challenge and return the exported CSV as bytes."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale='zh-CN', user_agent=USER_AGENT,
                                      accept_downloads=True)
        page = context.new_page()
        try:
            page.goto(HOME_URL, wait_until='domcontentloaded', timeout=60_000)
            wait_for_challenge(page)

            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / RAW_NAME
                with page.expect_download(timeout=60_000) as download_info:
                    try:
                        page.goto(EXPORT_URL, timeout=60_000)
                    except PlaywrightError:
                        # The export URL returns an attachment and aborts the
                        # navigation; the download still starts
                        pass
                download_info.value.save_as(target)
                return target.read_bytes()
        finally:
            browser.close()


def unescape(text: str) -> str:
    """Unescape the whole text.

    The export HTML-escapes cells. Decoding directly would turn &quot; inside a
    quoted cell into an unescaped quote and shift CSV fields, so &quot; is
    replaced by CSV's "" first and entities are decoded after, matching the old
    unescape.sh. Each field's quoting style is preserved.
    """
    return html.unescape(text.replace('&quot;', '""'))


def check(text: str) -> None:
    """Validate the export; a header mismatch means the challenge or permission page came back."""
    columns = [field.split('=', 1)[-1] for field in EXPORT_PARAMS['fields'].split(',')]
    header = text.split('\n', 1)[0]
    if header != ','.join(columns):
        sys.exit(f'Header mismatch, no CSV received: {header[:80]!r}')
    leftovers = ENTITY_PATTERN.findall(text)
    if leftovers:
        print(f'Warning: {len(leftovers)} entities left unescaped, e.g. {leftovers[0]}',
              file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description='Update the Arknights operator table')
    parser.add_argument('--from', dest='source', metavar='CSV', type=Path,
                        help='read a locally downloaded raw CSV instead of fetching')
    args = parser.parse_args()

    if args.source:
        print(f'Reading {args.source}')
        raw = args.source.read_bytes()
    else:
        print('Fetching prts.wiki (Anubis challenge, a few seconds)')
        raw = fetch_raw()

    text = unescape(raw.decode('utf-8-sig'))
    check(text)
    CSV_PATH.write_bytes(text.encode('utf-8'))
    print(f'Wrote {CSV_PATH} ({len(text.splitlines()) - 1} rows)')


if __name__ == '__main__':
    main()
