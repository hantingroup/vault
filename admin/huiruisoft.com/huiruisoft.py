"""huiruisoft 区划页的抓取与解析（https://www.huiruisoft.com/district/）。

站点按行政区逐级一张表，省、市、县、乡镇都有自己的页面：110000.html 是北京市的下级，
11/110101.html 是东城区的下级，710000.html 是台湾省的下级。
表头六列：区划代码、中文名称、电话区号、邮政编码、面积(km²)、政府驻地。
全国汇总页 2022.html 是一张两列的大表（行政区划代码、行政区划名称），把县及以上
的单位一次列完，台湾、香港、澳门也在里面。两种表都按表头认，不认死表名。
这里只做「抓下来、按表头拆成字典」，不套仓库格式；要对齐成仓库的表（台湾、香港
的码值和同级顺序）见 ../citypopulation.de/build.py。

页面缓存在 .cache/（--refresh 重下），page 里的斜杠照原样落成子目录。
"""

import re
import sys
import unicodedata
import urllib.request
from html import unescape
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / '.cache'
BASE = 'https://www.huiruisoft.com/district/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

TABLE_RE = re.compile(r'<table[^>]*>(.*?)</table>', re.S)
ROW_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S)
TAG_RE = re.compile(r'<[^>]+>')

# 两种页面的表头叫法不一样：区划页是「区划代码/中文名称」，全国汇总页是
# 「行政区划代码/行政区划名称」
CODE_FIELDS = ('区划代码', '行政区划代码')
NAME_FIELDS = ('中文名称', '行政区划名称')


def cache_path(page: str) -> Path:
    """page 形如 710000 或 71/710100，缓存保留同样的子目录"""
    return CACHE_DIR / f'{page}.html'


def fetch(page: str, refresh: bool = False) -> str:
    """下载并缓存，缓存是为了不每跑一次都打人家的站"""
    cache = cache_path(page)
    if cache.exists() and not refresh:
        return cache.read_text(encoding='utf-8')
    url = f'{BASE}{page}.html'
    print(f'下载 {url}', file=sys.stderr)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        html = response.read().decode('utf-8')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding='utf-8', newline='\n')
    return html


def parse(html: str) -> List[Dict[str, str]]:
    """页面上「代码 + 名称」那张表 -> [{表头: 值}]，保持页面顺序。

    一页可能有好几张表（单位简介、地图），按表头找那张真正的区划表：
    第一个单元格是代码列、第二个是名称列的那一行当表头，之后的行是数据。
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
    """去标签、去实体、NFC 归一化"""
    return unicodedata.normalize('NFC', unescape(TAG_RE.sub('', fragment))).strip()
