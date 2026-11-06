#!/usr/bin/env python3
"""Inverse of merge.py: split a merged table with a level column back into files.

    python split.py .merged.csv xzqh.csv ../citypopulation.de/台湾.csv@1,2,3:7

@ mirrors its meaning in merge.py. There, FILE@4 means the file has no level
column and gets one filled with 4; here the same marker means the column is
synthetic and is dropped from the output. So

    python merge.py a.csv@1 ../b.csv@4 > .merged.csv
    python split.py .merged.csv a.csv@1 b.csv@4

round-trips. A target without @ receives all remaining rows and keeps the level
column.

Rows are selected by level. To split one level across several files (Taiwan,
Hong Kong and Macau share levels 1-3 with the mainland), append a colon and code
prefixes to the level spec:

    python split.py .merged.csv xzqh.csv ../citypopulation.de/台湾.csv@1,2,3:7 ../citypopulation.de/香港.csv@1,2,3:81 ../dsec.gov.mo/澳门.csv@1,2,3:82

Prefix targets also pick up the mainland table's placeholder rows for those
regions (71 台湾省, 81 香港特别行政区, 82 澳门特别行政区), so give them to the
region tables or leave them in xzqh.csv, not both.

Targets with code prefixes keep the level column: rows are selected by code
rather than level, and the file still belongs to the "has level" kind
(台湾/香港/澳门.csv carry a level column).
"""

import argparse
import csv
import sys
from typing import Dict, List, Optional, Set, Tuple

FIELDS = ['level', 'parent', 'code', 'type', 'name']


def parse_spec(spec: str) -> Tuple[Set[str], Optional[Tuple[str, ...]]]:
    """'4' -> ({'4'}, None); '1,2,3:7,8' -> ({'1','2','3'}, ('7','8'))"""
    levels, _, prefixes = spec.partition(':')
    level_set = {level for level in levels.split(',') if level}
    prefix_tuple = tuple(p for p in prefixes.split(',') if p) if prefixes else None
    return level_set, prefix_tuple


def matches(row: Dict[str, str], levels: Set[str], prefixes: Optional[Tuple[str, ...]]) -> bool:
    if levels and row['level'] not in levels:
        return False
    if prefixes and not row['code'].startswith(prefixes):
        return False
    return True


def write(path: str, fields: List[str], rows: List[Dict[str, str]]) -> None:
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(fields)
        for row in sorted(rows, key=lambda r: r['code']):
            writer.writerow([row[field] for field in fields])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Split a merged level,parent,code,type,name table back into files.',
        epilog='Target syntax: PATH@LEVELS[:PREFIXES]. A level-only target drops the '
               'synthetic level column; a target with code prefixes keeps it. Rows go '
               'to targets in order; one target without @ takes the rest.')
    parser.add_argument('source', help='merged CSV with level,parent,code,type,name')
    parser.add_argument('targets', nargs='+', metavar='TARGET',
                        help='output file, optionally followed by @LEVELS[:PREFIXES]')
    args = parser.parse_args(argv)

    source, targets = args.source, args.targets
    with open(source, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            print(f"{source}: missing column(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
        rows = list(reader)

    rest: Optional[str] = None
    claimed = [False] * len(rows)

    # Assign @ targets first; the rows left over go to the target without @
    for target in targets:
        if '@' not in target:
            if rest is not None:
                print("Only one target may omit @; mark the others with @level", file=sys.stderr)
                return 2
            rest = target
            continue

        path, spec = target.split('@', 1)
        levels, prefixes = parse_spec(spec)
        if not levels and not prefixes:
            print(f"{target}: @ is not followed by levels or code prefixes", file=sys.stderr)
            return 2

        picked = [i for i, row in enumerate(rows)
                  if not claimed[i] and matches(row, levels, prefixes)]
        for i in picked:
            claimed[i] = True
        # A level spec marks a synthetic column and drops it on output; with a
        # code prefix the column exists in the source and is kept
        fields = FIELDS if prefixes else [f for f in FIELDS if f != 'level']
        write(path, fields, [rows[i] for i in picked])
        print(f"{path}: {len(picked)} row(s)", file=sys.stderr)

    if rest is not None:
        picked = [i for i in range(len(rows)) if not claimed[i]]
        write(rest, FIELDS, [rows[i] for i in picked])
        print(f"{rest}: {len(picked)} row(s)", file=sys.stderr)

    left = [rows[i]['code'] for i in range(len(rows)) if not claimed[i]]
    if left and rest is None:
        preview = ', '.join(left[:10]) + ('…' if len(left) > 10 else '')
        print(f"{len(left)} row(s) unassigned: {preview}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
