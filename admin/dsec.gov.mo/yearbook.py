"""Download the DSEC 《統計年鑑》 PDF and parse table 1.1, 「統計分區」.

The publication page is rendered by JS and has no direct file links. The release
metadata lives at
https://www.dsec.gov.mo/StatisticsJSON/AllReleases_zh-MO_General.json:
YearbookOfStatistics → Containers.YearbookOfStatistics.Publication.Yearly lists
the yearly editions; the file whose Type is .pdf is the full yearbook, and its
Link looks like /getAttachment/<GUID>/C_AE_PUB_2025_Y.aspx.

The table page 「面積按分區」 has four columns: number, Chinese name, Portuguese
name, area. Extracting by content stream interleaves the columns and mixes in
map labels, so text is taken by coordinates: the number, name and Portuguese
columns each form a vertical line at their own x, and a row shares one y. When a
long name wraps, the continuation has no number and joins the nearest number
row by y. Returns [(number, district name), ...] with numbers 1-26 as printed.

PDFs are cached in admin/.cache/dsec.gov.mo/ (--refresh redownloads the release
list and the PDF).
"""

import argparse
import csv
import json
import logging
import re
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from pypdf import PdfReader

# Rotated map labels make pypdf warn; they are not part of the table
logging.getLogger('pypdf').setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parent          # dsec.gov.mo
ADMIN_DIR = ROOT.parent                         # admin
CACHE_DIR = ADMIN_DIR / '.cache' / 'dsec.gov.mo'
CACHE_JSON = CACHE_DIR / 'releases_General.json'
SITE = 'https://www.dsec.gov.mo'
RELEASES_URL = f'{SITE}/StatisticsJSON/AllReleases_zh-MO_General.json'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# Table 1.1 titles; both must appear on the page (the contents page and other
# area tables also mention 統計分區)
HEADER = '統計分區'
TITLE = 'ÁREA DE SOLOS'

CJK_RE = re.compile(r'[㐀-䶿一-鿿]')
INT_RE = re.compile(r'\d{1,2}')
LATIN_RE = re.compile(r'[A-Za-z]')


def fetch(refresh: bool = False) -> Path:
    """Download the latest yearbook PDF and return its local path; reuse the cache when present."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if refresh or not CACHE_JSON.exists():
        _download(RELEASES_URL, CACHE_JSON)
    # The site's JSON has a BOM
    data = json.loads(CACHE_JSON.read_text(encoding='utf-8-sig'))
    year, link = _yearbook_pdf(data)
    path = CACHE_DIR / f'yearbook_{year}.pdf'
    if refresh or not path.exists():
        _download(urljoin(SITE, link), path)
    return path


def parse(path: Path) -> List[Tuple[int, str]]:
    """Parse table 1.1 into [(number, district name), ...]; raise on anything unexpected instead of guessing."""
    reader = PdfReader(str(path))
    chunks = _chunks(_table_page(reader))

    # The table starts at the 統計分區 header; the map is to its left, at a smaller x
    header = next((c for c in chunks if c[2] == HEADER), None)
    if header is None:
        raise ValueError(f'Header 「{HEADER}」 not found')
    x0, y0, _ = header
    region = [c for c in chunks if c[0] >= x0 - 10 and c[1] < y0]

    # Number column: plain digits running 1..N without gaps
    numbers = sorted((c for c in region if INT_RE.fullmatch(c[2])),
                     key=lambda c: -c[1])
    if len(numbers) < 2 or [int(c[2]) for c in numbers] != list(range(1, len(numbers) + 1)):
        raise ValueError(f'Unexpected number column: {[c[2] for c in numbers]}')

    spacing = numbers[0][1] - numbers[1][1]
    # The first row's name can sit slightly above its number, so half a row is
    # used as the top edge; the header is above it
    top = numbers[0][1] + spacing / 2
    num_right = max(c[0] for c in numbers)
    # The Portuguese column follows the Chinese column. Names containing both
    # Latin letters and CJK characters (新城A區) are not Portuguese
    latin = [c[0] for c in region
             if c[1] < top and c[0] > num_right + 5
             and LATIN_RE.search(c[2]) and not CJK_RE.search(c[2])]
    if not latin:
        raise ValueError('Portuguese column not found; the page layout may have changed')
    name_right = min(latin)

    grouped = {int(c[2]): [] for c in numbers}
    for x, y, text in region:
        if y >= top or x <= num_right + 5 or x >= name_right:
            continue
        if not CJK_RE.search(text):
            continue
        number = min(numbers, key=lambda c: abs(c[1] - y))
        # Text far from any number row is a horizontal map label (e.g. 「㸅˺」)
        if abs(number[1] - y) > 2 * spacing:
            continue
        grouped[int(number[2])].append((y, text))

    districts = []
    for number in sorted(grouped):
        parts = [text for _, text in sorted(grouped[number], key=lambda p: -p[0])]
        if not parts:
            raise ValueError(f'No name for number {number}')
        # Join wrapped cell lines without a separator
        districts.append((number, ''.join(parts)))
    return districts


def districts(refresh: bool = False, path: Optional[Path] = None) -> List[Tuple[int, str]]:
    """Fetch (or use the local --pdf) and parse; used by fetch.py."""
    return parse(path or fetch(refresh))


def _yearbook_pdf(data: dict) -> Tuple[int, str]:
    """Pick the newest release that has a PDF -> (year, PDF link)."""
    container = data['YearbookOfStatistics']['Containers']['YearbookOfStatistics']
    candidates = []
    for release in container['Publication']['Yearly']:
        for file in release.get('File') or []:
            if file.get('Type', '').lower() == '.pdf' and file.get('Link'):
                candidates.append((int(release['Year']), file['Link']))
                break
    if not candidates:
        raise ValueError('No yearbook PDF in the release list')
    return max(candidates)


def _table_page(reader: PdfReader):
    """Find the page containing table 1.1 by its titles."""
    for page in reader.pages:
        text = page.extract_text() or ''
        if HEADER in text and TITLE in text:
            return page
    raise ValueError(f'Table 1.1 ({HEADER} / {TITLE}) not found in the PDF')


def _chunks(page) -> List[Tuple[float, float, str]]:
    """Collect text chunks in content-stream order -> [(x, y, text)]."""
    chunks = []

    def visit(text, cm, tm, font_dict, font_size):
        text = text.strip()
        if text:
            chunks.append((tm[4], tm[5], text))

    page.extract_text(visitor_text=visit)
    return chunks


def _download(url: str, path: Path) -> None:
    print(f'Downloading {url}', file=sys.stderr)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=300) as response:
        path.write_bytes(response.read())


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Download the DSEC 《統計年鑑》 and parse table 1.1, 「統計分區」')
    parser.add_argument('-o', '--output', type=Path, help='write CSV here; defaults to stdout')
    parser.add_argument('--refresh', action='store_true',
                        help='redownload the release list and PDF')
    parser.add_argument('--pdf', type=Path, help='use a local PDF instead of downloading')
    args = parser.parse_args(argv)

    rows = districts(args.refresh, args.pdf)
    if args.output:
        with open(args.output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, lineterminator='\n')
            writer.writerow(['num', 'name'])
            writer.writerows(rows)
        print(f'{len(rows)} rows -> {args.output}', file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        writer = csv.writer(sys.stdout, lineterminator='\n')
        writer.writerow(['num', 'name'])
        writer.writerows(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
