"""Fetch huiruisoft district pages and write CSV using the page headers.

    python fetch.py                        # 2022 national summary, county level and above, to stdout
    python fetch.py 710000                 # Taiwan province
    python fetch.py 110000 -o beijing.csv  # Beijing's districts
    python fetch.py 71/710100              # children of Taipei
    python fetch.py 110000 -r              # Beijing and everything below it
    python fetch.py -r -o all.csv          # the whole country, every level (~40k pages)
    python fetch.py 710000 --refresh       # refetch instead of the cache

page is the path on the site; pages are cached in admin/.cache/huiruisoft.com/
(see huiruisoft.py). Without --recursive the page's own table is written with
its own headers. With --recursive the linked child pages are followed to the
bottom, and rows from every level are merged by code into one table with the
district page's columns.
"""

import argparse
import csv
import sys
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

from huiruisoft import child_page, fetch, parse, parse_with_links

# Rows from the national summary page use these headers for the same columns
ALIASES = {'行政区划代码': '区划代码', '行政区划名称': '中文名称'}
FIELDS = ['区划代码', '中文名称', '电话区号', '邮政编码', '面积(km²)', '政府驻地']


def walk(page: str, refresh: bool) -> List[Dict[str, str]]:
    """Collect the page's rows and those of every linked page below it.

    Rows are merged by code: levels reached both from a summary page and from
    their own page keep the fields either one supplies.
    """
    merged: Dict[str, Dict[str, str]] = {}
    seen = set()
    queue = deque([page])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        rows = parse_with_links(fetch(current, refresh))
        print(f'{current}: {len(rows)} rows', file=sys.stderr)
        for row, href in rows:
            row = {ALIASES.get(key, key): value for key, value in row.items()}
            code = row.get('区划代码', '')
            target = merged.setdefault(code, {})
            for key, value in row.items():
                if value:
                    target[key] = value
            if href:
                queue.append(child_page(current, href))
    return [merged[code] for code in sorted(merged)]


def write_csv(path: Optional[Path], fields: List[str], rows: List[Dict[str, str]]) -> None:
    """Write the rows to path, or to stdout when path is None."""
    if path:
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        print(f'{len(rows)} rows -> {path}', file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='Fetch huiruisoft district pages')
    ap.add_argument('page', nargs='?', default='2022',
                    help='page path on the site, e.g. 2022 (default), 710000 or 71/710100')
    ap.add_argument('-r', '--recursive', action='store_true',
                    help='follow the linked child pages and merge every level by code')
    ap.add_argument('-o', '--output', type=Path, help='write CSV here; defaults to stdout')
    ap.add_argument('--refresh', action='store_true', help='refetch pages instead of using the cache')
    args = ap.parse_args(argv)

    if args.recursive:
        rows, fields = walk(args.page, args.refresh), FIELDS
    else:
        rows = parse(fetch(args.page, args.refresh))
        fields = list(rows[0]) if rows else []
    if not rows:
        print('No divisions parsed from the page; check the page path or the page layout.',
              file=sys.stderr)
        return 1
    write_csv(args.output, fields, rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
