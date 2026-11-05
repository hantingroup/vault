#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 .output/澳门.csv：按 DSEC《統計年鑑二零二五年》表 1.1「統計分區」。

澳门不抓 citypopulation.de 的澳门页：那张表只有 24 个分区（少了 路氹填海區、
新城C區），还把新城A區单列成一个 Zone。这里的 26 个统计分区是 DSEC 的编号和
官方名字，抄在下面的 MO_UNITS 里，顺序就是官方编号 1-26。

名字切「专名:通名」，通名由 type 定，和 ../citypopulation.de/build.py 一样用
../scripts/gat_common.py 的规则；澳门的 type 不跟大陆的行政区划分类对齐，
照页面原文写繁体（堂区 -> 堂區）。

编码规则：
    省级      82
    地级      82 + 2 位（8201 澳門半島、8202 氹仔、8203 路環、8204 路氹城）
    县级      地级 + 2 位（820101）
    乡级      县级 + 3 位（820101001）

澳门在特别行政区和堂区之间没有一级行政区，8201-8204 这四行跟香港的三行一样是
编码表里的地理分组，不是建制单位。默认 type=區域、名字按仓库格式补通名
（2,82,8201,區域,澳門半島:區域）；--mo-regions=TYPE 换 type，--mo-regions=
（空）则 type 留空、名字不加通名。用 --no-mo-regions 可以不要这四行：
10 个县级单位直接挂 82，编成 820101-820110。

用法:
    python update.py                    # 四行地理分组，type=區域
    python update.py --mo-regions=地區   # 四行改用 地區 作 type
    python update.py --no-mo-regions     # 不补这四行
"""

import argparse
import csv
import sys
from pathlib import Path

# 共用的 type 与「专名:通名」在 admin/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from gat_common import SUFFIX, split_name, type_out

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / '.output'
OUT = OUTPUT_DIR / '澳门.csv'

# 最上面那行是本特别行政区，本仓库这边算省级。parent 是「大区」首位数字，
# 跟 dmfw 的约定一致：7 台湾 8 港澳
TOP = [1, '8', '82', '特别行政区', '澳門:特別行政區']

# 澳门没有地级行政区，这四行跟香港的三行一样是编码表里的地理分组：
# 8201 澳門半島、8202 氹仔、8203 路環、8204 路氹城。
MO_REGIONS = [
    ('8201', '澳門半島'),
    ('8202', '氹仔'),
    ('8203', '路環'),
    ('8204', '路氹城'),
]


def mo_region_rows(type_):
    rows = []
    for code, name in MO_REGIONS:
        suffix = SUFFIX.get(type_, type_)
        rows.append([2, '82', code, type_out(type_),
                     f'{name}:{suffix}' if type_ else name])
    return rows


# 澳门的三级、四级全按 DSEC《統計年鑑二零二五年》表 1.1「統計分區」抄，26 个分区
# 就是编号 1-26 那 26 行（水域、澳門大學新校區不在这张表里，不算统计分区）。
# 原表见 https://www.dsec.gov.mo/zh-MO/Home/Publication/YearbookOfStatistics 的 PDF
# 第 32 页（面積按分區，1-26 号跟面积一栏一一对得上）。每项是
# (region, 三级名字, type, [该节点下的统计分区官方名, ...])，顺序按官方编号：
#     1-6 花地瑪、7-10 聖安多尼、11-12 望德、13-15 大堂、16-17 風順、
#     18-22 氹仔（嘉模堂區）、23 路環（聖方濟各堂區）、24 路氹填海區、
#     25 新城A區及港珠澳大橋澳門口岸管理區、26 新城C區
# 三级名字沿用 citypopulation/huiruisoft 的叫法（它們把堂區写成所在的地名，
# 比如嘉模堂區写成 氹仔）；统计分区名字则是政府原文，一个字不改。
#
# 24-26 三个新填海区在 DSEC 的堂区表里「不適用」（不属任何堂区），各挂一个 区 节点，
# 节点底下就是它自己那一个统计分区。25 挂在 澳門半島（毗邻港珠澳大橋口岸，
# 民政上归花地瑪堂區），26 挂在 氹仔（民政上归嘉模堂區），24 挂在 路氹城。
MO_UNITS = [
    ('8201', '花地瑪', '堂区', [
        '青洲區', '台山區', '黑沙環及祐漢區', '黑沙環新填海區', '望廈及水塘區', '筷子基區',
    ]),
    ('8201', '聖安多尼', '堂区', [
        '林茂塘區', '高士德及雅廉訪區', '新橋區', '沙梨頭及大三巴區',
    ]),
    ('8201', '望德', '堂区', [
        '荷蘭園區', '東望洋區（松山區）',
    ]),
    ('8201', '大堂', '堂区', [
        '新口岸區', '外港及南灣湖新填海區', '中區',
    ]),
    ('8201', '風順', '堂区', [
        '下環區', '南西灣及主教山區',
    ]),
    ('8202', '氹仔', '堂区', [
        '海洋及小潭山區', '氹仔中心區', '大學及北安灣區', '北安及大潭山區', '氹仔舊城及馬場區',
    ]),
    ('8203', '路環', '堂区', [
        '路環區',
    ]),
    ('8204', '路氹填海', '区', [
        '路氹填海區',
    ]),
    ('8201', '新城A', '区', [
        '新城A區及港珠澳大橋澳門口岸管理區',
    ]),
    ('8202', '新城C', '区', [
        '新城C區',
    ]),
]


def mo_rows(used):
    """澳门的三级、四级行，按 MO_UNITS 生成。

    默认三级挂在那四行地理分组下面（820101-820106 澳門半島、820201-820202 氹仔、
    820301 路環、820401 路氹城），组内从 01 编起；--no-mo-regions 时不要那四行，
    10 个三级单位直接挂 82，编成 820101-820110。四级是上级码 + 3 位，从 001 编起。
    """
    rows = []
    counters = {}
    for i, (region, name, type_, districts) in enumerate(MO_UNITS, start=1):
        if ARGS.no_mo_regions:
            parent, code = '82', f'8201{i:02d}'
        else:
            parent = region
            n = counters[region] = counters.get(region, 0) + 1
            code = f'{region}{n:02d}'
        used.add(code)
        rows.append([3, parent, code, type_out(type_), split_name(name, type_)])
        for j, district in enumerate(districts, start=1):
            code4 = f'{code}{j:03d}'
            used.add(code4)
            rows.append([4, code, code4, type_out('统计区'),
                         split_name(district, '统计区')])
    return rows


def main():
    used = set()
    rows = [[TOP[0], TOP[1], TOP[2], type_out(TOP[3]), TOP[4]]]
    if not ARGS.no_mo_regions:
        rows += mo_region_rows(ARGS.mo_regions)
    used |= {row[2] for row in rows}
    rows += mo_rows(used)

    # code 撞车只可能是补号逻辑坏了，早点炸掉比悄悄出一份重码的表强
    seen = {}
    for level, parent, code, type_, name in rows:
        if code in seen:
            raise ValueError(f'{code} 撞车：{seen[code]} / {name}')
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
    print(f'{len(rows)} 条 -> {OUT}', file=sys.stderr)
    for (level, type_), n in sorted(counts.items()):
        print(f'  {level} 级 {type_ or "(无)"}: {n}', file=sys.stderr)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='生成澳门统计分区表')
    ap.add_argument('--mo-regions', nargs='?', const='', default='区域', metavar='TYPE',
                    help='补 澳門半島/氹仔/路環/路氹城 四行，堂区与三个新城区挂到 '
                         '8201-8204 下；默认 type=區域（澳門半島:區域），'
                         '--mo-regions=TYPE 换 type，--mo-regions= （空）则 type 留空、'
                         '名字不加通名')
    ap.add_argument('--no-mo-regions', action='store_true',
                    help='不补这四行：10 个县级单位直接挂 82，编成 820101-820110')
    ARGS = ap.parse_args()
    main()
