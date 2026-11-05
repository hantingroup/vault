"""抓 citypopulation.de 的行政区列表页，按页面原样输出上下级、状态和名称。

    python fetch.py https://www.citypopulation.de/en/taiwan/admin/ -o taiwan.csv
    python fetch.py https://www.citypopulation.de/en/china/admin/ | head

列：parent,status,name,native

    parent  上一级单位在本页的英文名，最上面一级留空
    status  页面自己的状态标签（Special Municipality、County…），不翻译
    name    页面英文名
    native  页面母语名；页面没有这一列时留空（例如德国）

不套仓库的「专名:通名」，也不补码——这一层留给需要对齐的脚本（见 build.py）。
页面是一张平表：<tr class="rname"> 开头的是这一级，它下面到下一个 rname 之前
的行是下一级，所以一页只看两级，更深的层级在各自的子页上。
缓存落在 .cache/citypopulation/（--refresh 重下）。
"""

import argparse
import csv
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / '.cache' / 'citypopulation'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
FIELDS = ['parent', 'status', 'name', 'native']

ROW_RE = re.compile(r'<tr[^>]*>.*?</tr>', re.S)
TR_ATTRS_RE = re.compile(r'<tr([^>]*)>')
NAME_RE = re.compile(r'<td class="rname"[^>]*>.*?<span itemprop="name">(.*?)</span>', re.S)
STATUS_RE = re.compile(r'class="rstatus">(.*?)</td>')
NATIVE_RE = re.compile(r'class="rnative"><span itemprop="name">(.*?)</span>')


def cache_path(url: str) -> Path:
    """缓存文件名取路径最后两段：/en/china/hongkong/admin/ -> hongkong_admin.html"""
    parts = [part for part in urllib.parse.urlparse(url).path.split('/') if part]
    return CACHE_DIR / f'{"_".join(parts[-2:])}.html'


def fetch(url: str, refresh: bool = False) -> str:
    """下载并缓存，缓存是为了不每跑一次都打人家的站"""
    cache = cache_path(url)
    if cache.exists() and not refresh:
        return cache.read_text(encoding='utf-8')
    print(f'下载 {url}', file=sys.stderr)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode('utf-8')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(page, encoding='utf-8', newline='\n')
    return page


def parse(page: str) -> List[Dict[str, str]]:
    """页面平表 -> [{parent, status, name, native}]，保持页面顺序"""
    rows = []
    parent = ''
    for tr in ROW_RE.findall(page):
        name_match = NAME_RE.search(tr)
        if not name_match:
            continue
        name = _text(name_match.group(1))
        status_match = STATUS_RE.search(tr)
        status = _text(status_match.group(1)) if status_match else ''
        native_match = NATIVE_RE.search(tr)
        native = _text(native_match.group(1)) if native_match else ''
        if 'class="rname"' in TR_ATTRS_RE.search(tr).group(1):
            parent = name
            rows.append({'parent': '', 'status': status, 'name': name, 'native': native})
        else:
            rows.append({'parent': parent, 'status': status,
                         'name': name, 'native': native})
    return rows


def _text(fragment: str) -> str:
    """去实体、NFC 归一化。页面的「路環」用的是兼容汉字 U+F937，同一处地名会有
    两种看着一样的码点，归一化后才能跟别的表对上"""
    return unicodedata.normalize('NFC', unescape(fragment))


def write_csv(rows: List[Dict[str, str]], stream) -> None:
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='抓 citypopulation.de 的行政区列表页')
    parser.add_argument('url', help='例如 https://www.citypopulation.de/en/taiwan/admin/')
    parser.add_argument('-o', '--output', type=Path, help='输出 CSV；不给就写到 stdout')
    parser.add_argument('--refresh', action='store_true', help='重新下载页面，不用缓存')
    args = parser.parse_args(argv)

    rows = parse(fetch(args.url, args.refresh))
    if not rows:
        print('页面上没解析到单位，检查地址，或页面结构变了', file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, 'w', newline='', encoding='utf-8') as f:
            write_csv(rows, f)
        print(f'{len(rows)} 条 -> {args.output}', file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        write_csv(rows, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
