"""Fetch huiruisoft district pages and write CSV using the page headers.

    python scripts/update.py 710000                 # Taiwan province, to stdout
    python scripts/update.py 810000 -o hongkong.csv # Hong Kong
    python scripts/update.py 71/710100              # children of Taipei
    python scripts/update.py 110000                 # children of Beijing
    python scripts/update.py 11/110101              # children of Dongcheng
    python scripts/update.py 2022 -o 2022.csv       # county level and above
    python scripts/update.py 710000 --refresh       # refetch instead of the cache

page is the path on the site; pages are cached in .cache/ (see huiruisoft.py).
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional

from huiruisoft import fetch, parse


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='Fetch huiruisoft district pages')
    ap.add_argument('page', help='page path on the site, e.g. 710000 or 71/710100')
    ap.add_argument('-o', '--output', type=Path, help='write CSV here; defaults to stdout')
    ap.add_argument('--refresh', action='store_true', help='refetch the page instead of using the cache')
    args = ap.parse_args(argv)

    rows = parse(fetch(args.page, args.refresh))
    if not rows:
        print('No divisions parsed from the page; check the page path or the page layout.',
              file=sys.stderr)
        return 1
    fields = list(rows[0])
    if args.output:
        with open(args.output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        print(f'{len(rows)} rows -> {args.output}', file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
