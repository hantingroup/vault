"""Fetch citypopulation.de administrative list pages and output them as shown.

    cd admin/citypopulation.de
    python fetch.py https://www.citypopulation.de/en/taiwan/admin/ -o taiwan.csv
    python fetch.py https://www.citypopulation.de/en/china/admin/ | head
    python fetch.py https://www.citypopulation.de/en/china/admin/ -r -o china.csv

Columns: parent,level,id,status,name,alt,native,wiki,wikidata,area,density,
pop_<C|E>_<date>…,url

    parent    English name of the parent group row; empty on the group rows
              themselves and on the page's top unit
    level     the row's tbody level: 0 for admin0 (the page's top unit), 1 for
              admin1, 2 for admin2…
    id        citypopulation's id for the row, the digits of id="i3408"
    status    the page's own status label (Special Municipality, County…), untranslated
    name      English name from the page, the romanized form when it gives two
    alt       other names in brackets on the same cell, ";"-joined
    native    native-language name from the page; empty when the page has no such
              column (e.g. Germany)
    wiki      Wikipedia article title (data-wiki)
    wikidata  Wikidata item id (data-wd)
    area      area in km² (data-area)
    density   population density (data-density)
    pop_*     one column per population column on the page, named after the
              header's "C 2020-11-01" (C census, E estimate)
    url       absolute URL of the unit's detail page

Fields the page does not carry stay empty. Rows are written sorted by id
(numeric ids in numeric order, the top unit with no id first, other ids last)
rather than in page order. The population columns vary with the page and come
from its header; the page's other tables (gender, age groups, …) are map
summaries, not unit data, and are not read.

Names are not marked 「专名:通名」 and codes are not filled in; the scripts that
align the data handle that.

The page is a flat table: a row with class="rname" starts a level, and the rows
up to the next rname belong to it, so one page covers two levels. Deeper levels
are on separate section pages linked from the country's index page (e.g. China's
per-province counties page and townships page); --recursive walks those section
pages and merges their rows by id. Unit detail pages (id__name) hold population
data, not subdivisions, and are not followed. Pages are cached in
admin/.cache/citypopulation.de/ (--refresh refetches).
"""

import argparse
import csv
import heapq
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent          # citypopulation.de
ADMIN_DIR = ROOT.parent                         # admin
CACHE_DIR = ADMIN_DIR / '.cache' / 'citypopulation.de'
BASE_URL = 'https://www.citypopulation.de/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

TABLE_RE = re.compile(r'<table[^>]*\bid="tl"[^>]*>(.*?)</table>', re.S)
THEAD_RE = re.compile(r'<thead>(.*?)</thead>', re.S)
TBODY_RE = re.compile(r'<tbody([^>]*)>(.*?)</tbody>', re.S)
ROW_RE = re.compile(r'<tr([^>]*)>(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<t([dh])([^>]*)>(.*?)</t[dh]>', re.S)
ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
CLASS_RE = re.compile(r'class="([^"]*)"')
TAG_RE = re.compile(r'<[^>]+>')
BRACKET_RE = re.compile(r'\[(.*?)\]', re.S)
HREF_RE = re.compile(r'href="([^"]*)"')

# Columns every page has, in output order. The page-dependent columns (one per
# population date, then url) follow them.
FIXED_FIELDS = ['parent', 'level', 'id', 'status', 'name', 'alt', 'native',
                'wiki', 'wikidata', 'area', 'density']


def cache_path(url: str) -> Path:
    """Cache name from the whole path, so section pages cannot collide:
    /en/china/townships/anhui/admin/ -> en_china_townships_anhui_admin.html
    """
    parts = [part for part in urllib.parse.urlparse(url).path.split('/') if part]
    name = '_'.join(parts) or 'index'
    return CACHE_DIR / f'{re.sub(r"[^\w.-]+", "_", name)}.html'


def fetch(url: str, refresh: bool = False) -> str:
    """Download the page and cache it; reuse the cache unless refresh is set."""
    cache = cache_path(url)
    if cache.exists() and not refresh:
        return cache.read_text(encoding='utf-8')
    print(f'Fetching {url}', file=sys.stderr)
    # The pages link to non-ASCII paths (ānhuī_shĕng); urllib needs them quoted
    request = urllib.request.Request(urllib.parse.quote(url, safe=':/?=&%'), headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        page = response.read().decode('utf-8')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(page, encoding='utf-8', newline='\n')
    return page


def parse(page: str) -> List[Dict[str, str]]:
    """Turn the main table into rows of all the fields the page carries, keeping page order."""
    table = TABLE_RE.search(page)
    if not table:
        return []
    columns = _columns(table.group(1))
    fields = _fields(columns)
    rows: List[Dict[str, str]] = []
    parent = ''
    for tbody_attrs, tbody in TBODY_RE.findall(table.group(1)):
        level = _level(tbody_attrs)
        for tr_attrs, tr in ROW_RE.findall(tbody):
            cells = CELL_RE.findall(tr)
            if not cells:
                continue
            row = {field: '' for field in fields}
            row['level'] = level
            for (_, cell_attrs, inner), column in zip(cells, columns):
                attrs = dict(ATTR_RE.findall(cell_attrs))
                if column == 'name':
                    row['name'], row['alt'] = _name_parts(inner)
                    row['id'] = _row_id(attrs.get('id', ''))
                    row['wiki'] = attrs.get('data-wiki', '')
                    row['wikidata'] = attrs.get('data-wd', '')
                    row['area'] = attrs.get('data-area', '')
                    row['density'] = attrs.get('data-density', '')
                elif column == 'url':
                    row['url'] = _url(inner)
                else:
                    row[column] = _text(inner)
            if 'rname' in _classes(tr_attrs):
                parent = row['name']
            elif level != '0':
                row['parent'] = parent
            rows.append(row)
    return rows


def _columns(table_html: str) -> List[str]:
    """Canonical field name for each column of the main table, in page order."""
    thead = THEAD_RE.search(table_html)
    if not thead:
        return []
    columns: List[str] = []
    for _, attrs_fragment, _ in CELL_RE.findall(thead.group(1)):
        attrs = dict(ATTR_RE.findall(attrs_fragment))
        classes = attrs.get('class', '').split()
        coltype = attrs.get('data-coltype', '')
        if 'sc' in classes:
            name = 'url'
        elif coltype == 'name':
            name = 'name'
        elif coltype == 'status':
            name = 'status'
        elif coltype == 'othername':
            name = 'native'
        elif coltype == 'pop':
            # data-colhead is like "C 2020-11-01"; data-coldate drops the C/E.
            head = attrs.get('data-colhead') or attrs.get('data-coldate') or ''
            name = 'pop_' + _identifier(head)
        else:
            # Unseen column types still get a column, so nothing is silently dropped.
            name = _identifier(coltype or (classes[0] if classes else ''))
        name = _unique(name, columns)
        columns.append(name)
    return columns


def _fields(columns: List[str]) -> List[str]:
    """Output order: the fixed fields, then the page's extra columns, url last."""
    extra = [column for column in columns if column not in ('name', 'status', 'native')]
    if 'url' in extra:
        extra.remove('url')
        extra.append('url')
    fields = list(FIXED_FIELDS)
    fields += [column for column in extra if column not in fields]
    return fields


def _identifier(text: str) -> str:
    """'C 2020-11-01' -> 'C_2020-11-01' so it can be used as a column name."""
    return re.sub(r'[^0-9A-Za-z-]+', '_', text).strip('_') or 'column'


def _unique(name: str, taken: List[str]) -> str:
    """Keep column names distinct when a page repeats one."""
    candidate, n = name, 2
    while candidate in taken:
        candidate = f'{name}_{n}'
        n += 1
    return candidate


def _level(tbody_attrs: str) -> str:
    """tbody class="admin2" -> 2; empty when the tbody is not an admin level."""
    for cls in _classes(tbody_attrs):
        if cls.startswith('admin'):
            return cls[len('admin'):]
    return ''


def _classes(attrs_fragment: str) -> List[str]:
    match = CLASS_RE.search(attrs_fragment)
    return match.group(1).split() if match else []


def _name_parts(fragment: str) -> Tuple[str, str]:
    """Split a name cell into the displayed name and the bracketed other names.

    The cell looks like ``<a…><span itemprop="name">Gaoxiong Shi</span></a>
    [<span itemprop="name">Kaohsiung Shih</span>]`` or, when the page puts the
    romanization first, ``Zhōnghuá Rénmín Gònghéguó [<span…>People's Republic
    of China</span>]``. The text outside the brackets is the page's main name.
    """
    alts = [_text(alt) for alt in BRACKET_RE.findall(fragment)]
    name = _text(BRACKET_RE.sub('', fragment))
    return name, ';'.join(alt for alt in alts if alt)


def _row_id(raw: str) -> str:
    """id="i3408" -> 3408, id="iA" -> A; the page prefixes every id with i, and
    what follows is the code the detail URLs are built from."""
    return raw[1:] if raw.startswith('i') and len(raw) > 1 else raw


def _url(fragment: str) -> str:
    """Absolute URL of the row's detail page; empty for the top unit."""
    match = HREF_RE.search(fragment)
    return urllib.parse.urljoin(BASE_URL, match.group(1)) if match else ''


def _text(fragment: str) -> str:
    """Drop tags, strip entities and apply NFC. The page writes 路環 with a
    compatibility ideograph (U+F937), which NFC folds onto the standard code
    point so the name matches other tables."""
    return unicodedata.normalize('NFC', unescape(TAG_RE.sub('', fragment))).strip()


def sort_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Order rows by id: numeric ids first, in numeric order, other ids after them.

    parse keeps page order, which the parent lookup needs; the output is sorted
    so that the same units come out in the same order from any page. The page's
    ids are a unit's digits (id="i3408" -> 3408), so numeric ids sort
    hierarchically: 34 before 3408 before 340801. Some ids carry a letter suffix
    (131103100m); those sort with their number. The page's own top unit has no
    id and stays first; any other non-numeric id goes last.
    """
    def key(row: Dict[str, str]):
        value = row['id']
        if not value:
            return (0, 0, '')
        match = re.match(r'(\d+)(.*)', value)
        if match:
            return (1, int(match.group(1)), match.group(2))
        return (2, 0, value)
    return sorted(rows, key=key)


def root_of(url: str) -> str:
    """Index page of the territory a listing page belongs to.

    .../en/taiwan/admin/ -> .../en/taiwan/ and .../en/china/hongkong/admin/ ->
    .../en/china/hongkong/. The section pages that go below the listing page are
    linked from there.
    """
    parts = urllib.parse.urlparse(url)
    segments = [s for s in parts.path.split('/') if s]
    if segments and segments[-1] in ('admin', 'cities'):
        segments = segments[:-1]
    path = '/' + '/'.join(segments) + '/' if segments else '/'
    return urllib.parse.urlunparse(parts._replace(path=path, query='', fragment=''))


def before_subentities(page: str) -> str:
    """Truncate a country index page where a second <h1> introduces sub-entities.

    China's index page has a second <h1> for the Special Administrative Regions.
    Their pages use their own id namespace (Macau's "23" is not Heilongjiang), so
    they are not part of the country crawl; each has its own index page.
    """
    first = page.find('<h1')
    second = page.find('<h1', first + 3) if first >= 0 else -1
    return page if second < 0 else page[:second]


def path_depth(url: str) -> int:
    """Number of path segments, used to visit parent pages before their sections."""
    return len([s for s in urllib.parse.urlparse(url).path.split('/') if s])


def section_links(page: str, base_url: str, root_url: str) -> List[str]:
    """Directory links on a page, under the territory's index.

    Unit detail pages end in ``id__name`` and are skipped; they hold population
    data, not subdivisions. Other directory links are section pages (admin/,
    townships/ or townships/beijing/admin/); the non-admin ones are only walked
    through to discover the listing pages below them.
    """
    host = urllib.parse.urlparse(root_url).netloc
    root_path = urllib.parse.urlparse(root_url).path
    links = []
    for href in HREF_RE.findall(page):
        url = urllib.parse.urljoin(base_url, href)
        parts = urllib.parse.urlparse(url)
        if parts.netloc != host or not parts.path.startswith(root_path):
            continue
        segments = [s for s in parts.path.split('/') if s]
        if not segments:
            continue
        last = segments[-1]
        if '__' in last or '.' in last:
            continue
        links.append(urllib.parse.urlunparse(parts._replace(query='', fragment='')))
    return links


def page_offset(rows: List[Dict[str, str]], known: Dict[str, int]) -> int:
    """How much a page's tbody levels are shifted from the country page's levels.

    A section page restarts at admin1 for the units it shows, so the same unit's
    level differs between pages. The offset is the most common difference from
    levels already seen; a page whose units are all new keeps the page levels.
    """
    offsets: Dict[int, int] = {}
    for row in rows:
        if not row['id'] or not row['level'].isdigit():
            continue
        if row['id'] in known:
            offset = known[row['id']] - int(row['level'])
            offsets[offset] = offsets.get(offset, 0) + 1
    return max(offsets, key=offsets.get) if offsets else 0


def merge_rows(pages: List[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    """Merge the rows of several pages by id, filling empty fields from later pages.

    The first page's values win, so a unit keeps the parent it had on the
    country page. Rows without an id are the pages' own top units; only the
    first page's top unit is kept.
    """
    merged: Dict[str, Dict[str, str]] = {}
    top = False
    for rows in pages:
        for row in rows:
            code = row['id']
            if not code and row['level'] == '0':
                if top:
                    continue
                top = True
            if not code:
                code = f'\0{row["level"]}\0{row["name"]}'
            target = merged.setdefault(code, dict.fromkeys(row, ''))
            for key, value in row.items():
                if value and not target.get(key):
                    target[key] = value
    return list(merged.values())


def walk(url: str, refresh: bool = False) -> List[Dict[str, str]]:
    """Parse the page and every administrative listing page under the territory.

    The listing page covers two levels; the country index links to the section
    pages that hold the levels below (China's per-province counties pages and
    per-city townships pages, for instance), sometimes through an index page
    such as townships/. Only pages whose last segment is ``admin`` are read;
    the rest are walked through for their links. Rows are merged by id and
    their levels shifted onto the country page's scale. Pages are visited
    shallowest first, so a section page's parent rows are known before it needs
    the offset.
    """
    root = root_of(url)
    seen = set()
    queue = [(path_depth(url), 0, url), (path_depth(root), 1, root)]
    seq = 2
    pages = []
    known: Dict[str, int] = {}
    while queue:
        _, _, current = heapq.heappop(queue)
        if current in seen:
            continue
        seen.add(current)
        page = fetch(current, refresh)
        rows = parse(page)
        if [s for s in urllib.parse.urlparse(current).path.split('/') if s][-1] != 'admin':
            print(f'{current}: links only', file=sys.stderr)
            rows = []
        else:
            offset = page_offset(rows, known)
            for row in rows:
                if row['id'] and row['level'].isdigit():
                    row['level'] = str(int(row['level']) + offset)
                    known.setdefault(row['id'], int(row['level']))
            print(f'{current}: {len(rows)} rows', file=sys.stderr)
        pages.append(rows)
        links_from = before_subentities(page) if current == root else page
        for link in section_links(links_from, current, root):
            if link not in seen:
                heapq.heappush(queue, (path_depth(link), seq, link))
                seq += 1
    return merge_rows(pages)


def write_csv(rows: List[Dict[str, str]], stream) -> None:
    # Recursive output can mix pages with different population columns
    fields = list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Fetch citypopulation.de administrative list pages')
    parser.add_argument('url', help='e.g. https://www.citypopulation.de/en/taiwan/admin/')
    parser.add_argument('-r', '--recursive', action='store_true',
                        help="follow the territory's section pages and merge every level by id")
    parser.add_argument('-o', '--output', type=Path, help='write CSV here; defaults to stdout')
    parser.add_argument('--refresh', action='store_true', help='refetch the page instead of using the cache')
    args = parser.parse_args(argv)

    rows = walk(args.url, args.refresh) if args.recursive else parse(fetch(args.url, args.refresh))
    rows = sort_rows(rows)
    if not rows:
        print('No units parsed from the page; check the URL or the page layout.', file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, 'w', newline='', encoding='utf-8') as f:
            write_csv(rows, f)
        print(f'{len(rows)} rows -> {args.output}', file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        write_csv(rows, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
