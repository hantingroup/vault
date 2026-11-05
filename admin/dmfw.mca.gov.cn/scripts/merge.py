#!/usr/bin/env python3
"""Merge level,parent,code,type,name CSVs into one table on stdout.

    python scripts/merge.py 省市区县.csv 乡镇街道.csv@4 > .merged.csv

A file given as FILE@LEVEL gets its level column filled with LEVEL, for files
that do not carry one. split.py is the inverse.
"""

import argparse
import csv
import sys
from operator import itemgetter
from typing import List, Optional

FIELDS = ['level', 'parent', 'code', 'type', 'name']


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Merge administrative division CSVs into one table on stdout.',
        epilog='The inverse is split.py.')
    parser.add_argument('files', nargs='+', metavar='FILE[@LEVEL]',
                        help='CSV to merge; @LEVEL fills the level column for files without one')
    args = parser.parse_args(argv)

    rows = []
    for arg in args.files:
        filename, level = arg.split('@') if '@' in arg else (arg, None)
        with open(filename, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if level is not None:
                    row['level'] = level
                rows.append([row[field] for field in FIELDS])

    rows.sort(key=itemgetter(FIELDS.index('code')))

    writer = csv.writer(sys.stdout, lineterminator='\n')
    writer.writerow(FIELDS)
    writer.writerows(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
