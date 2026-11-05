"""抓 huiruisoft 的区划页，按页面表头输出 CSV。

    python update.py 710000                 # 台湾省的下级，写到 stdout
    python update.py 810000 -o hongkong.csv # 香港
    python update.py 71/710100              # 台北市的下级
    python update.py 110000                 # 北京市的下级（各区）
    python update.py 11/110101              # 东城区的下级（各街道）
    python update.py 2022 -o 2022.csv       # 全国县及以上，一张平表
    python update.py 710000 --refresh       # 重新下载，不用缓存

page 是站点上的路径；页面缓存在 .cache/（见 huiruisoft.py）。
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional

from huiruisoft import fetch, parse


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='抓 huiruisoft 的区划页')
    ap.add_argument('page', help='站点上的路径，例如 710000 或 71/710100')
    ap.add_argument('-o', '--output', type=Path, help='输出 CSV；不给就写到 stdout')
    ap.add_argument('--refresh', action='store_true', help='重新下载页面，不用缓存')
    args = ap.parse_args(argv)

    rows = parse(fetch(args.page, args.refresh))
    if not rows:
        print('页面上没解析到区划，检查 page，或页面结构变了', file=sys.stderr)
        return 1
    fields = list(rows[0])
    if args.output:
        with open(args.output, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        print(f'{len(rows)} 条 -> {args.output}', file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == '__main__':
    sys.exit(main())
