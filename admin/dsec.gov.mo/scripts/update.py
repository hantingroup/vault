#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate .output/澳门.csv from DSEC 《統計年鑑》 table 1.1 「統計分區」.

The numbers and names of the 26 statistical districts are parsed from the
yearbook PDF (see yearbook.py) instead of being hard-coded, so additions and
removals in a new edition surface as an error against the grouping in
refs/macau.json rather than as silently missing rows.

Macau is not fetched from the citypopulation.de page: that table has only 24
districts (missing 路氹填海區 and 新城C區) and lists 新城A區 as a separate Zone.
The 26 districts here use DSEC's numbers and official names, ordered by the
official 1-26.

Names are split into 「专名:通名」, with the generic part chosen by type using the
rules in gat_common.py in this directory; Macau types do not follow the mainland
classification and stay in traditional Chinese.

Code layout:
    省級      82
    地級      82 + 2 digits (8201 澳門半島, 8202 氹仔, 8203 路環, 8204 路氹城)
    縣級      地級 + 2 digits (820101)
    鄉級      縣級 + 3 digits (820101001)

Macau has no administrative level between the SAR and the parishes; rows
8201-8204 are geographic groups in the code table, like Hong Kong's three rows,
not administrative units. They default to type=區域 and get a marked name
(2,82,8201,區域,澳門半島:區域); --mo-regions=TYPE changes the type, and an empty
--mo-regions= leaves type and name unmarked. --no-mo-regions omits these four
rows, attaching the 10 county-level units directly to 82 as 820101-820110.

Usage (from the source directory):
    python scripts/update.py                    # four geographic rows, type=區域
    python scripts/update.py --mo-regions=地區   # type for those rows
    python scripts/update.py --no-mo-regions     # omit the four rows
    python scripts/update.py --refresh           # redownload the yearbook PDF
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List, Optional

# The shared administrative levels live at the admin/ level.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))
from level import Level

import yearbook
from gat_common import split_name, suffix_of

DATA = json.loads((ROOT / 'refs' / 'macau.json').read_text(encoding='utf-8'))
OUTPUT_DIR = ROOT / '.output'
OUT = OUTPUT_DIR / '澳门.csv'

# The first row is the SAR itself, which counts as province level here. parent is
# the first digit of the region, matching dmfw: 7 Taiwan, 8 Hong Kong/Macau
TOP = [Level(str(DATA['top'][0])), DATA['top'][1], DATA['top'][2],
       DATA['top'][3], DATA['top'][4]]

# Macau has no prefecture-level divisions; these four rows are geographic groups
# in the code table: 8201 澳門半島, 8202 氹仔, 8203 路環, 8204 路氹城.
MO_REGIONS = [tuple(row) for row in DATA['mo_regions']]


def mo_region_rows(type_):
    rows = []
    for code, name in MO_REGIONS:
        suffix = suffix_of(type_)
        rows.append([Level.PREFECTURE, '82', code, type_,
                     f'{name}:{suffix}' if type_ else name])
    return rows


# Which parish or new town each statistical district belongs to. Table 1.1 gives
# only the 26 district numbers and names, not the parish, so the mapping is
# maintained in refs/macau.json; the keys are the district names as printed in
# the yearbook.
# 花地瑪, 聖安多尼, 望德, 大堂 and 風順 are the parishes of 澳門半島; 氹仔 is
# 嘉模堂區 and 路環 is 聖方濟各堂區. The three new reclamation districts 24-26
# are 「不適用」 (no parish) in DSEC's parish table and each hangs under a 区
# node: 25 新城A區 borders the Hong Kong-Zhuhai-Macau Bridge port and belongs to
# 花地瑪堂區 administratively, under 8201; 26 新城C區 belongs to 嘉模堂區, under
# 8202; 24 路氹填海區 goes under 路氹城.
#
# (region, level-3 name, type). Codes within a region are assigned 01, 02… in
# the order listed here, so reordering changes codes.
GROUPS = [tuple(row) for row in DATA['groups']]

# Yearbook district name -> group name in GROUPS. A renamed district in a new
# edition fails here
DISTRICT_GROUP = DATA['district_group']


def mo_rows(districts, no_mo_regions=False):
    """Level-3 and level-4 rows for Macau.

    districts is yearbook's [(number, district name)], with level-4 rows ordered
    by number under their level-3 parent. By default level-3 rows hang under the
    four geographic rows (820101-820106 澳門半島, 820201-820202 氹仔, 820301
    路環, 820401 路氹城), numbered from 01 within each group; with no_mo_regions
    the four rows are omitted and the 10 level-3 units attach directly to 82 as
    820101-820110. Level-4 codes are the parent code plus 3 digits, from 001.
    """
    bad_groups = set(DISTRICT_GROUP.values()) - {name for _, name, _ in GROUPS}
    if bad_groups:
        raise ValueError(f'Groups in DISTRICT_GROUP but not in GROUPS: {sorted(bad_groups)}')

    members = {name: [] for _, name, _ in GROUPS}
    for _, district in districts:
        group = DISTRICT_GROUP.get(district)
        if group is None:
            raise ValueError(
                f'Yearbook district 「{district}」 has no group; add it to DISTRICT_GROUP')
        members[group].append(district)
    missing = set(DISTRICT_GROUP) - {district for _, district in districts}
    if missing:
        raise ValueError(f'Districts missing from the yearbook: {", ".join(sorted(missing))}')
    empty = [name for name, names in members.items() if not names]
    if empty:
        raise ValueError(f'Groups with no districts: {", ".join(empty)}')

    rows = []
    counters = {}
    for i, (region, name, type_) in enumerate(GROUPS, start=1):
        if no_mo_regions:
            parent, code = '82', f'8201{i:02d}'
        else:
            parent = region
            n = counters[region] = counters.get(region, 0) + 1
            code = f'{region}{n:02d}'
        rows.append([Level.COUNTY, parent, code, type_, split_name(name, type_)])
        for j, district in enumerate(members[name], start=1):
            code4 = f'{code}{j:03d}'
            rows.append([Level.TOWNSHIP, code, code4, '統計區',
                         split_name(district, '統計區')])
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Generate the Macau statistical district table from the DSEC yearbook.',
        epilog='Writes .output/澳门.csv.')
    parser.add_argument('--mo-regions', nargs='?', const='', default='區域', metavar='TYPE',
                        help='add the 澳門半島/氹仔/路環/路氹城 rows and hang parishes and '
                             'new towns under 8201-8204; TYPE is their type (default 區域); '
                             'an empty TYPE leaves type and names unmarked')
    parser.add_argument('--no-mo-regions', action='store_true',
                        help='omit those rows; the 10 county-level units attach to 82 as 820101-820110')
    parser.add_argument('--refresh', action='store_true', help='redownload the yearbook PDF')
    parser.add_argument('--pdf', type=Path, help='use a local PDF instead of downloading')
    args = parser.parse_args(argv)

    districts = yearbook.districts(args.refresh, args.pdf)
    print(f'{len(districts)} statistical districts (1-{districts[-1][0]})', file=sys.stderr)

    rows = [TOP]
    if not args.no_mo_regions:
        rows += mo_region_rows(args.mo_regions)
    rows += mo_rows(districts, args.no_mo_regions)

    seen = {}
    for level, parent, code, type_, name in rows:
        if code in seen:
            raise ValueError(f'{code} duplicated: {seen[code]} / {name}')
        seen[code] = name

    rows.sort(key=lambda r: r[2])
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OUT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['level', 'parent', 'code', 'type', 'name'])
        writer.writerows(rows)
    counts = {}
    for level, _, _, type_, _ in rows:
        counts[(level, type_)] = counts.get((level, type_), 0) + 1
    print(f'{len(rows)} rows -> {OUT}', file=sys.stderr)
    for (level, type_), n in sorted(counts.items()):
        print(f'  level {level} {type_ or "(none)"}: {n}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
