"""Fetch and parse huiruisoft district pages (https://www.huiruisoft.com/district/).

The site has one table per administrative level: 110000.html lists Beijing's
children, 11/110101.html lists Dongcheng's, 710000.html lists Taiwan's.
Columns: 区划代码, 中文名称, 电话区号, 邮政编码, 面积(km²), 政府驻地.
The national summary page 2022.html is a two-column table (行政区划代码,
行政区划名称) covering county level and above, including Taiwan, Hong Kong and
Macau. Both kinds are recognized by their headers rather than by page name.

This module only fetches pages and splits rows into dicts; alignment to the
repository format (Taiwan/Hong Kong code values and ordering) is in
../citypopulation.de/build.py. Pages are cached in
admin/.cache/huiruisoft.com/; a page string like 71/710100 becomes a
subdirectory.
"""

import posixpath
import re
import sys
import unicodedata
import urllib.request
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent          # huiruisoft.com
ADMIN_DIR = ROOT.parent                         # admin
CACHE_DIR = ADMIN_DIR / '.cache' / 'huiruisoft.com'
BASE = 'https://www.huiruisoft.com/district/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

TABLE_RE = re.compile(r'<table[^>]*>(.*?)</table>', re.S)
ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S)
TAG_RE = re.compile(r'<[^>]+>')
HREF_RE = re.compile(r'href="([^"]+)"')
# Child pages are relative numeric paths (110000.html, 11/110101.html); the
# summary page also links a download (/data/...) and the layout links out
CHILD_RE = re.compile(r'[0-9/]+\.html')

# The two page kinds use different headers: 区划代码/中文名称 on district pages,
# 行政区划代码/行政区划名称 on the national summary
CODE_FIELDS = ('区划代码', '行政区划代码')
NAME_FIELDS = ('中文名称', '行政区划名称')


def cache_path(page: str) -> Path:
    """page is like 710000 or 71/710100; the cache keeps the same subdirectories."""
    return CACHE_DIR / f'{page}.html'


def fetch(page: str, refresh: bool = False) -> str:
    """Download the page and cache it; reuse the cache unless refresh is set."""
    cache = cache_path(page)
    if cache.exists() and not refresh:
        return cache.read_text(encoding='utf-8')
    url = f'{BASE}{page}.html'
    print(f'Fetching {url}', file=sys.stderr)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode('utf-8')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding='utf-8', newline='\n')
    return html


def parse(html: str) -> List[Dict[str, str]]:
    """Turn the page's code+name table into [{header: value}], keeping page order.

    A page may contain several tables (summary, map); the district table is the
    one whose header row starts with a code column and a name column.
    """
    header, rows = _code_table(html)
    return [dict(zip(header, cells)) for cells, _ in rows
            if cells and cells[0].isdigit()]


def parse_with_links(html: str) -> List[Tuple[Dict[str, str], Optional[str]]]:
    """parse(), plus each row's child-page href (None when the row has none).

    The href is relative to the page it was found on; child_page() resolves it.
    """
    header, rows = _code_table(html)
    return [(dict(zip(header, cells)),
             next((h for h in hrefs if CHILD_RE.fullmatch(h)), None))
            for cells, hrefs in rows if cells and cells[0].isdigit()]


def child_page(page: str, href: str) -> str:
    """Resolve a row's href against its page: '110000' + '11/110101.html' -> '11/110101'."""
    return posixpath.normpath(posixpath.join(posixpath.dirname(page), href[:-len('.html')]))


def _code_table(html: str):
    """(header cells, [(data cells, hrefs)]) for the page's code+name table."""
    for table in TABLE_RE.findall(html):
        rows = []
        for tr in ROW_RE.findall(table):
            rows.append(([_text(cell) for cell in CELL_RE.findall(tr)],
                         HREF_RE.findall(tr)))
        for i, (cells, _) in enumerate(rows):
            if len(cells) >= 2 and cells[0] in CODE_FIELDS and cells[1] in NAME_FIELDS:
                return cells, rows[i + 1:]
    return [], []


def _text(fragment: str) -> str:
    """Strip tags and entities and apply NFC normalization."""
    return unicodedata.normalize('NFC', unescape(TAG_RE.sub('', fragment))).strip()
