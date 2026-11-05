"""Fetch and parse huiruisoft district pages (https://www.huiruisoft.com/district/).

The site has one table per administrative level: 110000.html lists Beijing's
children, 11/110101.html lists Dongcheng's, 710000.html lists Taiwan's.
Columns: 区划代码, 中文名称, 电话区号, 邮政编码, 面积(km²), 政府驻地.
The national summary page 2022.html is a two-column table (行政区划代码,
行政区划名称) covering county level and above, including Taiwan, Hong Kong and
Macau. Both kinds are recognized by their headers rather than by page name.

This module only fetches pages and splits rows into dicts; alignment to the
repository format (Taiwan/Hong Kong code values and ordering) is in
../../citypopulation.de/scripts/hmt/build.py. Pages are cached in .cache/; a
page string like 71/710100 becomes a subdirectory.
"""

import re
import sys
import unicodedata
import urllib.request
from html import unescape
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / '.cache'
BASE = 'https://www.huiruisoft.com/district/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

TABLE_RE = re.compile(r'<table[^>]*>(.*?)</table>', re.S)
ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S)
TAG_RE = re.compile(r'<[^>]+>')

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
    for table in TABLE_RE.findall(html):
        rows = [[_text(cell) for cell in CELL_RE.findall(tr)]
                for tr in ROW_RE.findall(table)]
        for i, cells in enumerate(rows):
            if len(cells) >= 2 and cells[0] in CODE_FIELDS and cells[1] in NAME_FIELDS:
                return [dict(zip(cells, row)) for row in rows[i + 1:]
                        if row and row[0].isdigit()]
    return []


def _text(fragment: str) -> str:
    """Strip tags and entities and apply NFC normalization."""
    return unicodedata.normalize('NFC', unescape(TAG_RE.sub('', fragment))).strip()
