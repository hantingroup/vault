#!/usr/bin/env python3
"""把 省市区县.csv 与 乡镇街道.csv 合成一份带 level 的 .merged.csv。

省市区县.csv 自带 level（1/2/3），乡镇街道.csv 没有，按 4 补上。
合并后整体按 code 升序排列——code 长度即层级，短码在前，
所以同一个省、市、县下的各级区划挨在一起。
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / '.merged.csv'
FIELDS = ['level', 'parent', 'code', 'type', 'name']

# (文件名, 补的 level)；None 表示文件本身已带 level 列
SOURCES = [
    ('省市区县.csv', None),
    ('乡镇街道.csv', '4'),
]

def main():
    rows = []
    for filename, level in SOURCES:
        with open(ROOT / filename, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if level is not None:
                    row['level'] = level
                rows.append([row[field] for field in FIELDS])

    rows.sort(key=lambda row: row[FIELDS.index('code')])

    with open(OUT, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(FIELDS)
        writer.writerows(rows)

    print(f'{OUT.name}: {len(rows)} 行')


if __name__ == '__main__':
    main()
