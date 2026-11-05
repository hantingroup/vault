#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Convert the citypopulation.de Taiwan and Hong Kong pages into repository format
and write .output/台湾.csv and .output/香港.csv.

Page fetching and parsing are done by ../fetch.py (a generic fetcher returning
the page's raw fields); this script does the three alignment steps:
    - status label -> type (Special Municipality -> 直轄市, District Council ->
      區議會…). Types do not follow the mainland classification (Taiwan has no
      prefecture level, a province-administered city is a 市, and below a county
      comes a 縣轄市) and stay in traditional Chinese like the page names
    - names are split into 「专名:通名」, with the generic part chosen by type;
      names without one get it appended
    - code values and same-level ordering come from huiruisoft's district
      tables; the ordering table is kept in refs/order.json and rebuilt with
      --refresh-order from https://www.huiruisoft.com/district/
      (page cache: ../../../huiruisoft.com/.cache/)

Codes given in the table are copied as-is: Taiwan's 7101-7109 for municipalities
and province-administered cities, 7122/7124/… for counties (gaps are kept, no 00
suffix added), and Tainan's districts skip 710503/710505. Where the table has
only an order and no code (Taiwan township level, Hong Kong), codes are generated
by the repository rules:

    prefecture = province + 2 digits (71 -> 7101)
    county     = parent + 2 digits (7101 -> 710101); Hong Kong attaches directly
                 to province level, filling in the middle two digits
                 (81 -> 810101); Taiwan's counties are 4 digits like
                 municipalities (71 -> 7122)
    township   = county + 3 digits, first digit by category (see SERIES;
                 7122 -> 7122100)

The page supplies only units and names; units that do not match or are unknown
are placed last at their level in page order and a warning is printed to stderr.
The pages use traditional characters while huiruisoft uses simplified ones, so
the two are matched through the name pairs in refs/order.json: each entry keeps
the huiruisoft name and the page name it was mapped to. A refresh reuses those
pairs instead of converting characters; a new unit with no pair is reported and
needs one added to refs/order.json.

Hong Kong's 18 districts are grouped under 8101 香港島 / 8102 九龍 / 8103 新界 by
HK_DISTRICTS and numbered from 01 within each region in huiruisoft order. These
three rows are not administrative units; they default to type=區域 and get a
marked name (2,81,8102,區域,九龍:區域). --hk-regions=TYPE changes the type, an
empty --hk-regions= leaves type and name unmarked, and --no-hk-regions omits the
three rows, attaching the 18 districts to 81 as 810101-810118.

Macau is not handled here: DSEC's statistical districts differ from these pages,
see ../../../dsec.gov.mo/scripts/update.py.

Usage (from the source directory):
    python scripts/hmt/build.py               # keep page names and types
    python scripts/hmt/build.py --refresh     # redownload the pages
    python scripts/hmt/build.py --refresh-order   # rebuild .cache/order.json
    python scripts/hmt/build.py --hk-regions=地區  # type for the three rows
    python scripts/hmt/build.py --no-hk-regions    # omit the three rows
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent      # scripts/hmt
SCRIPTS_DIR = HERE.parent                   # scripts
ROOT = SCRIPTS_DIR.parent                   # citypopulation.de
ADMIN_DIR = ROOT.parent

# fetch.py sits in scripts/; huiruisoft and the shared level enum live under
# admin/.
for path in (HERE, SCRIPTS_DIR, ADMIN_DIR / 'huiruisoft.com' / 'scripts', ADMIN_DIR):
    sys.path.insert(0, str(path))

from fetch import fetch, parse
from gat_common import proper_of, split_name, suffix_of
from huiruisoft import fetch as fetch_huiruisoft, parse as parse_huiruisoft
from level import Level

DATA = json.loads((ROOT / 'refs' / 'taiwan_hongkong.json').read_text(encoding='utf-8'))
CACHE_DIR = ROOT / '.cache'
OUTPUT_DIR = ROOT / '.output'
OUT_TAIWAN = OUTPUT_DIR / '台湾.csv'
OUT_HONGKONG = OUTPUT_DIR / '香港.csv'

# The top row is the country/SAR itself, which counts as province level here.
# Taiwan's name and type follow GB/T 2260 (710000 臺灣省); Hong Kong uses the
# page's "Special Administrative Region" -> 特別行政區. parent is the first digit
# of the region, matching dmfw: 7 Taiwan, 8 Hong Kong/Macau
TOPS = {page: [Level(str(row[0])), *row[1:]] for page, row in DATA['tops'].items()}
TOPS_CODE = {page: row[2] for page, row in TOPS.items()}

# Hong Kong's 18 districts have no official county-level codes (the GB/T 2260
# Hong Kong table is missing). The common table below fills in prefecture level:
# 8101 香港島, 8102 九龍, 8103 新界, with the 18 districts under them. These three
# are geographic groups in the code table, not administrative units; they default
# to type=區域 with a marked name (九龍:區域), like the non-administrative units
# in 行政管理区.csv. --hk-regions=TYPE changes the type; an empty --hk-regions=
# leaves type empty and names unmarked; --no-hk-regions omits the three rows and
# attaches the 18 districts to 81 as 810101-810118.
HK_REGIONS = [tuple(row) for row in DATA['hk_regions']]


def hk_region_rows(type_):
    rows = []
    for code, name in HK_REGIONS:
        suffix = suffix_of(type_)
        rows.append([Level.PREFECTURE, '81', code, type_,
                     f'{name}:{suffix}' if type_ else name])
    return rows

# proper name -> region. Codes within a region follow huiruisoft order (see the
# '81/3' group in the ordering table), numbered from 01. Page names are
# traditional, so the keys are too.
HK_DISTRICTS = DATA['hk_districts']

PAGES = {
    'taiwan': 'https://www.citypopulation.de/en/taiwan/admin/',
    'hongkong': 'https://www.citypopulation.de/en/china/hongkong/admin/',
}

# Page status label -> (repository type, level). Levels follow the repository:
# 2 prefecture, 3 county, 4 township. Taiwan's labels are from
# https://www.citypopulation.de/en/taiwan/admin/: 直轄市, 省轄市, 縣. Below a
# Taiwan county are 縣轄市/鎮/鄉 (township level); below a city are 市轄區
# (county level)
TYPES = {
    'taiwan': {
        'Special Municipality': ('直轄市', Level.PREFECTURE),
        # Taiwan has no prefecture-level city; a province-administered city is
        # 市, and a city under a county is 縣轄市
        'Provincial City': ('市', Level.PREFECTURE),
        'County (Xian)': ('縣', Level.COUNTY),
        'City District (Qu)': ('市轄區', Level.COUNTY),
        'City (Shi)': ('縣轄市', Level.TOWNSHIP),
        'Urban Township (Zhen)': ('鎮', Level.TOWNSHIP),
        'Rural Township (Xiang)': ('鄉', Level.TOWNSHIP),
    },
    # Hong Kong is not a city; its 18 districts are District Council areas, so
    # 市辖区 does not apply
    'hongkong': {
        'District Council': ('區議會', Level.COUNTY),
        'Constituency Area': ('選區', Level.TOWNSHIP),
    },
}

def key_of(parent, level):
    """Parent code used when generating a code.

    Prefecture-level units attach to the province, so the parent is the province
    itself. Hong Kong's 18 districts attach directly to province level and need
    the middle two digits filled in (810101); those digits are placeholders.
    Taiwan's county codes are already 4 digits (7122 宜蘭) and sit at the same
    level as 7101-7109, so nothing is filled in. Otherwise the parent code is the
    parent.
    """
    if level == Level.COUNTY and len(parent) == 2 and parent != '71':
        return parent + '01'
    return parent


def new_code(parent, level, type_, used):
    """Generate a code by the repository rules:

        prefecture = province + 2 digits (71 -> 7101)
        county     = parent + 2 digits (7101 -> 710101; a Taiwan county attaches
                     directly to 71, so 71 -> 7122)
        township   = county + 3 digits, first digit by category (see SERIES;
                     7122 -> 7122100)
    """
    parent = key_of(parent, level)
    if level == Level.TOWNSHIP:
        start, width = SERIES[type_], 3
    else:
        start, width = 1, 2
    for n in range(start, 10 ** width):
        code = f'{parent}{n:0{width}d}'
        if code not in used:
            return code
    raise ValueError(f'No codes left under {parent}')


# The last three digits of a township code start with a category digit:
# street 0, town 1, township 2, quasi-township units 4-5. The units here go in
# the closest series: Taiwan's 縣轄市 shares the town series (both are urban
# units; the standard series has no separate slot for 市, so the two share a
# range and are distinguished by type), and Hong Kong's 選區 use series 0
SERIES = {'縣轄市': 100, '鎮': 100, '鄉': 200, '選區': 1}

# Same-level ordering comes from huiruisoft's district tables
# (https://www.huiruisoft.com/district/); the ordering table lives in
# refs/order.json and is rebuilt with --refresh-order rather than hard-coded
# (huiruisoft page cache: ../../../huiruisoft.com/.cache/).
# Shape: {"parent code/level": [{"code": ..., "name": ..., "page": ..., "huiruisoft": ...}, ...]}:
#   name       traditional proper name as on the page, matching unit['proper']
#   page       full page name (distinguishes 新竹市 from 新竹縣, which share a proper name)
#   code       copied code (Taiwan 7101/7122, districts 710101…); empty means order only
#   huiruisoft full name as shown on the site; a refresh matches entries by it
# Units missing from the table (units the site does not have yet) go last, in
# page order.
ORDER_PATH = ROOT / 'refs' / 'order.json'

ORDER = None


def huiruisoft_rows(page, refresh):
    """huiruisoft district page -> [(code, simplified full name)] in site order."""
    rows = parse_huiruisoft(fetch_huiruisoft(page, refresh))
    return [(row['区划代码'], row['中文名称']) for row in rows
            if row.get('区划代码', '').isdigit()]


# The top row is the country/region itself, not a subordinate unit
TOP_STATUSES = {'Republic', 'Special Administrative Region'}


def page_rows(page, refresh=False):
    """Return [parent page name, own name, status], skipping the top country/SAR row.

    parse returns English names; huiruisoft and the ordering table use native
    names, so they are mapped here. The parent row appears before its children in
    parsed, so the native name can be looked up.
    """
    parsed = parse(fetch(PAGES[page], refresh))
    # Only group rows (empty parent) carry the parent's English name; a child can
    # share the parent's English name (苗栗縣 and 苗栗市 are both Miaoli), so the
    # mapping is built from group rows only
    native_of = {row['name']: row['native'] for row in parsed if not row['parent']}
    rows = []
    for row in parsed:
        if row['status'] in TOP_STATUSES:
            continue
        parent_name = native_of.get(row['parent']) if row['parent'] else None
        rows.append((parent_name, row['native'], row['status']))
    return rows


def level_of(page, status):
    """Page status label -> (repository type, level); an unknown label raises."""
    if status not in TYPES[page]:
        raise ValueError(f'{page}: unknown status label {status!r}')
    return TYPES[page][status]


def page_units(refresh=False):
    """All units on the citypopulation pages, used to find traditional names when rebuilding the ordering table."""
    units = []
    for page in ('taiwan', 'hongkong'):
        for parent_name, name, status in page_rows(page, refresh):
            type_, level = level_of(page, status)
            full = split_name(name, type_)
            units.append({'page': page, 'parent': parent_name, 'name': name,
                          'type': type_, 'level': level, 'full': full,
                          'proper': proper_of(full)})
    return units


def match_rows(rows, units, old):
    """Match huiruisoft rows against page units, returning ([(code, site name, unit)], unmatched).

    rows   [(huiruisoft code, huiruisoft full name)], site order
    units  page units in this group
    old    entries from refs/order.json; each holds a huiruisoft name and the
           page name it was matched to
    """
    # Match by page name, not proper name: 新竹市 and 新竹縣 share 新竹
    by_page = {u['name']: u for u in units}
    page_of = {e['huiruisoft']: e['page'] for e in old if e.get('page') in by_page}
    matched, unmatched = [], []
    for code, site_name in rows:
        page = page_of.get(site_name)
        if page not in by_page:
            unmatched.append(site_name)
            continue
        matched.append((code, site_name, by_page[page]))
    return matched, unmatched


def fetch_order(refresh=False, city_refresh=False):
    """Rebuild the ordering table from huiruisoft and write refs/order.json.

    refresh refetches the huiruisoft pages; city_refresh refetches the
    citypopulation pages. Known huiruisoft/page name pairs from the existing
    table are reused; new names without a pair are reported. Returns the table
    that was written.
    """
    units = page_units(city_refresh)
    old = {}
    if ORDER_PATH.exists():
        try:
            old = json.loads(ORDER_PATH.read_text(encoding='utf-8'))
        except ValueError:
            print(f'Warning: cannot read {ORDER_PATH}; treating it as absent', file=sys.stderr)
    old_entries = [entry for entries in old.values() for entry in entries]

    order = {}
    top = {}
    for page, page_code in (('taiwan', '710000'), ('hongkong', '810000')):
        page_top = [u for u in units if u['page'] == page and u['parent'] is None]
        rows = huiruisoft_rows(page_code, refresh)
        matched, unmatched = match_rows(rows, page_top, old_entries)
        top[page] = matched
        for code, site_name, unit in matched:
            group = f'{TOPS_CODE[page]}/{unit["level"]}'
            order.setdefault(group, []).append({
                'code': code[:4] if page == 'taiwan' else '',
                'name': unit['proper'],
                'page': unit['name'],
                'huiruisoft': site_name,
            })
        for site_name in unmatched:
            print(f'Warning: {site_name} from huiruisoft {page_code} is not on the page, skipping',
                  file=sys.stderr)

    # Taiwan city/county child pages: a city has districts (county level), a
    # county has townships (township level)
    for code, _, parent_unit in top['taiwan']:
        prefix = code[:4]
        children = [u for u in units
                    if u['page'] == 'taiwan' and u['parent'] == parent_unit['name']]
        if not children:
            print(f'Warning: {prefix} {parent_unit["name"]} has no children on the page, skipping',
                  file=sys.stderr)
            continue
        levels = sorted({u['level'] for u in children})
        if len(levels) != 1:
            raise ValueError(f'{prefix} children have mixed levels: {levels}')
        level = levels[0]
        rows = huiruisoft_rows(f'71/{prefix}00', refresh)
        matched, unmatched = match_rows(rows, children, old_entries)
        group = f'{prefix}/{level}'
        for huiruisoft_code, site_name, child in matched:
            order.setdefault(group, []).append({
                # Districts copy huiruisoft's 6-digit code; townships only borrow
                # the order and get a generated code
                'code': huiruisoft_code if parent_unit['level'] == Level.PREFECTURE else '',
                'name': child['proper'],
                'page': child['name'],
                'huiruisoft': site_name,
            })
        for site_name in unmatched:
            print(f'Warning: {site_name} from huiruisoft 71/{prefix}00 is not on the page, skipping',
                  file=sys.stderr)

    ORDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORDER_PATH.write_text(json.dumps(order, ensure_ascii=False) + '\n',
                          encoding='utf-8', newline='\n')
    print(f'Ordering table: {sum(map(len, order.values()))} entries in {len(order)} groups '
          f'-> {ORDER_PATH}', file=sys.stderr)
    return order


def load_order():
    """Load the ordering table; build it first if absent."""
    global ORDER
    if ORDER is None:
        if ORDER_PATH.exists():
            ORDER = json.loads(ORDER_PATH.read_text(encoding='utf-8'))
        else:
            print(f'{ORDER_PATH} not found; building the huiruisoft ordering table first',
                  file=sys.stderr)
            ORDER = fetch_order()
    return ORDER


def ordered(page, units, parent, level):
    """Order a group of same-level units by the ordering table, returning their codes.

    Returns [(unit, code)]. Matched units come first in site order with the code
    from the table ('' means order only); unmatched units go last in page order
    with code None. When a table exists, mismatches in either direction (units
    missing from the table, table entries missing from the page) are warned
    about.
    """
    order = load_order().get(f'{parent}/{level}', [])
    if not order:
        return [(unit, None) for unit in units]
    rank, codes = {}, {}
    for i, entry in enumerate(order):
        rank[entry['name']] = i
        codes[entry['name']] = entry['code']
    seen = set()
    tagged = []
    for i, unit in enumerate(units):
        key = unit['proper']
        if key in rank:
            seen.add(key)
            tagged.append((rank[key], 0, i, unit, codes[key] or None))
        else:
            print(f'Warning: {page} {parent}/{level}: {unit["full"]} is not in the '
                  f'ordering table; placing it last and generating a code', file=sys.stderr)
            tagged.append((len(order), 1, i, unit, None))
    for entry in order:
        if entry['name'] not in seen:
            print(f'Warning: ordering table {parent}/{level}: {entry["name"]} '
                  f'is not on the page, skipping', file=sys.stderr)
    tagged.sort(key=lambda item: item[:3])
    return [(unit, code) for *_, unit, code in tagged]


def build(page, used, refresh=False, no_hk_regions=False):
    """Turn one page's data into [level, parent, code, type, name] rows."""
    parsed = []
    for parent_name, name, status in page_rows(page, refresh):
        type_, level = level_of(page, status)
        full = split_name(name, type_)
        parsed.append({'parent_name': parent_name, 'name': name, 'type': type_,
                       'level': level, 'full': full, 'proper': proper_of(full)})

    # Group in page order; a parent row always precedes its children, so the
    # parent code is known first
    groups, group_order = {}, []
    for unit in parsed:
        if unit['parent_name'] not in groups:
            groups[unit['parent_name']] = []
            group_order.append(unit['parent_name'])
        groups[unit['parent_name']].append(unit)

    rows = []
    code_of = {}
    for parent_name in group_order:
        units = groups[parent_name]
        # Parent of a second-level unit: Taiwan's cities and counties attach
        # directly to 71 (a county is province-administered), Hong Kong's to 81
        parent = TOPS_CODE[page] if parent_name is None else code_of[parent_name]
        for level in sorted({unit['level'] for unit in units}):
            same_level = [u for u in units if u['level'] == level]
            if page == 'hongkong' and parent_name is None and not no_hk_regions:
                # The 18 districts are ordered by huiruisoft first, then grouped
                # by region and numbered from 01 within each: 810101-810104
                # 香港島, 810201-810205 九龍, 810301-810309 新界
                counters = {}
                for unit, _ in ordered(page, same_level, TOPS_CODE[page], level):
                    region = HK_DISTRICTS[unit['proper']]
                    n = counters[region] = counters.get(region, 0) + 1
                    code = f'{region}{n:02d}'
                    code_of[unit['name']] = code
                    used.add(code)
                    rows.append([level, region, code, unit['type'],
                                 unit['full']])
                continue
            for unit, code in ordered(page, same_level, parent, level):
                if code is None:
                    code = new_code(parent, level, unit['type'], used)
                code_of[unit['name']] = code
                used.add(code)
                rows.append([level, parent, code, unit['type'],
                             unit['full']])
    return rows


def top_rows(page):
    """Repository-format row for the top unit (臺灣省, 香港特別行政區)."""
    level, parent, code, type_, name = TOPS[page]
    return [[level, parent, code, type_, name]]


def write_table(rows, path):
    """Sort, check for duplicates and write one table."""
    seen = {}
    for level, parent, code, type_, name in rows:
        if code in seen:
            raise ValueError(f'{path.name}: duplicate code {code}: {seen[code]} / {name}')
        seen[code] = name

    rows.sort(key=lambda r: r[2])
    path.parent.mkdir(exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['level', 'parent', 'code', 'type', 'name'])
        writer.writerows(rows)
    counts = {}
    for level, _, _, type_, _ in rows:
        counts[(level, type_)] = counts.get((level, type_), 0) + 1
    print(f'{len(rows)} rows -> {path}', file=sys.stderr)
    for (level, type_), n in sorted(counts.items()):
        print(f'  level {level} {type_ or "(none)"}: {n}', file=sys.stderr)


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(
        description='Generate the Taiwan and Hong Kong tables from citypopulation.de.',
        epilog='Writes .output/台湾.csv and .output/香港.csv.')
    parser.add_argument('--refresh', action='store_true',
                        help='redownload the citypopulation pages instead of using the cache')
    parser.add_argument('--refresh-order', action='store_true',
                        help='redownload the huiruisoft district tables and rebuild .cache/order.json')
    parser.add_argument('--hk-regions', nargs='?', const='', default='區域', metavar='TYPE',
                        help='add the 香港島/九龍/新界 rows; TYPE is their type (default 區域); '
                             'an empty TYPE leaves type and names unmarked')
    parser.add_argument('--no-hk-regions', action='store_true',
                        help='omit those rows; the 18 districts attach directly to 81')
    args = parser.parse_args(argv)

    if args.refresh_order:
        fetch_order(refresh=True, city_refresh=args.refresh)

    used = set()
    rows = top_rows('taiwan')
    used |= {row[2] for row in rows}
    rows += build('taiwan', used, refresh=args.refresh)
    write_table(rows, OUT_TAIWAN)

    used = set()
    rows = top_rows('hongkong')
    if not args.no_hk_regions:
        rows += hk_region_rows(args.hk_regions)
    used |= {row[2] for row in rows}
    rows += build('hongkong', used, refresh=args.refresh,
                  no_hk_regions=args.no_hk_regions)
    write_table(rows, OUT_HONGKONG)


if __name__ == '__main__':
    main()
