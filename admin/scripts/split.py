#!/usr/bin/env python3
"""merge.py 的反操作：把带 level 的合并表按层级拆回几个文件。

    python split.py .merged.csv 省市区县.csv 乡镇街道.csv@4

`@` 的写法和 merge.py 对称：那边的「乡镇街道.csv@4」是说这份文件本来没有 level 列、
按第 4 级补上；这边同一个记号表示「这一列是补出来的，拆出去时删掉」。所以

    python merge.py 省市区县.csv 乡镇街道.csv@4 > .merged.csv
    python split.py .merged.csv 省市区县.csv 乡镇街道.csv@4

一拆一合能得回原样。不带 @ 的目标文件收下其余所有行，并保留 level 列。

行由 level 区分；同一层级要拆成两个文件时（例如台港澳与内地同在 1–3 级），
在 level 后面接冒号和 code 前缀：

    python split.py .merged.csv 省市区县.csv 台湾.csv@1,2,3:7 香港.csv@1,2,3:81 澳门.csv@1,2,3:82 乡镇街道.csv@4

带了 code 前缀的目标保留 level 列——这时区分行的依据是 code，不是 level，
文件本身还是「有 level 的那一类」（台湾/香港/澳门.csv 就带 level 列）。
"""

import csv
import sys
from typing import Dict, List, Optional, Set, Tuple

FIELDS = ['level', 'parent', 'code', 'type', 'name']
CODE_INDEX = FIELDS.index('code')


def parse_spec(spec: str) -> Tuple[Set[str], Optional[Tuple[str, ...]]]:
    """'4' -> ({'4'}, None)；'1,2,3:7,8' -> ({'1','2','3'}, ('7','8'))"""
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


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    source, targets = argv[0], argv[1:]
    with open(source, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        missing = set(FIELDS) - set(reader.fieldnames or [])
        if missing:
            print(f"{source} 缺少列：{'、'.join(sorted(missing))}", file=sys.stderr)
            return 2
        rows = list(reader)

    rest: Optional[str] = None
    claimed = [False] * len(rows)

    # 先分配带 @ 的目标，剩下的给不带 @ 的那个
    for target in targets:
        if '@' not in target:
            if rest is not None:
                print("只能有一个不带 @ 的目标文件，其余用 @层级 指明", file=sys.stderr)
                return 2
            rest = target
            continue

        path, spec = target.split('@', 1)
        levels, prefixes = parse_spec(spec)
        if not levels and not prefixes:
            print(f"{target}：@ 后面没有给层级或 code 前缀", file=sys.stderr)
            return 2

        picked = [i for i, row in enumerate(rows)
                  if not claimed[i] and matches(row, levels, prefixes)]
        for i in picked:
            claimed[i] = True
        # 层级指的是「这一列是补出来的」；按 code 分拣时这一列本来就存在
        fields = FIELDS if prefixes else [f for f in FIELDS if f != 'level']
        write(path, fields, [rows[i] for i in picked])
        print(f"{path}: {len(picked)} 行", file=sys.stderr)

    if rest is not None:
        picked = [i for i in range(len(rows)) if not claimed[i]]
        write(rest, FIELDS, [rows[i] for i in picked])
        print(f"{rest}: {len(picked)} 行", file=sys.stderr)

    left = [rows[i]['code'] for i in range(len(rows)) if not claimed[i]]
    if left and rest is None:
        preview = '、'.join(left[:10]) + ('…' if len(left) > 10 else '')
        print(f"{len(left)} 行没有归属：{preview}", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
