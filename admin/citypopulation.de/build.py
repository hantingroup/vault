#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 citypopulation.de 的台湾、香港页面转成仓库格式，写到 .output/台湾.csv、香港.csv。

页面抓取和解析用同目录的 fetch.py（通用抓取器，输出页面原始字段），这里只做
对齐仓库的三件事：
    - 状态标签 -> type（Special Municipality -> 直轄市，District Council -> 區議會…）。
      type 不跟大陆的行政区划分类对齐（台湾没有地级市，省辖市就是「市」，
      县下是「縣轄市」），跟名字一样照页面原文写繁体
    - 名字切「专名:通名」，通名由 type 定；页面没写通名的补上
    - 码值与同级顺序取自 huiruisoft 的区划表，顺序表缓存在 .cache/order.json，
      由 --refresh-order 从 https://www.huiruisoft.com/district/ 抓下来重建
      （页面缓存见 ../huiruisoft.com/.cache/）

表里给了码的照抄：台湾的直辖市/省辖市 7101-7109、县 7122/7124/…（那边跳号这边也跳，
不加 00 尾巴）、台南的区跳过 710503/710505。表里只有顺序没有码的（台湾乡级、香港），
按本仓库规则现编：

    地级 = 省级 + 2 位（71 -> 7101）
    县级 = 上级 + 2 位（7101 -> 710101）；香港直接挂省级，补中间两位
           （81 -> 810101）；台湾的县跟直辖市同为 4 位（71 -> 7122）
    乡级 = 县级 + 3 位，首位按类别分段（见 SERIES；7122 -> 7122100）

页面只提供单位和名字；表里对不上或不认识的单位排在同级最后，按页面顺序补号，
并往 stderr 打一条警告。建表时用源码里的繁简对照表把页面名转成简体跟 huiruisoft
对（只收页面用到的差异字，不需要装 zhconv）；表建好以后刷新先按旧条目里的简体名认，
平时跑根本不转。
香港的 18 区按 HK_DISTRICTS 挂到 8101 香港島 / 8102 九龍 / 8103 新界 下面，
各 region 内再按 huiruisoft 的顺序从 01 编起。这三行不是建制单位，默认
type=區域、名字按仓库格式补通名（2,81,8102,區域,九龍:區域）；
--hk-regions=TYPE 可以换成别的 type，--hk-regions= （空）则 type 留空、名字不加通名。
用 --no-hk-regions 可以不要这三行：18 区直接挂 81，按 huiruisoft 顺序编成 810101-810118。

澳门不在这个脚本里：DSEC 的统计分区与这里的页面口径不同，见 ../dsec.gov.mo/update.py。

用法:
    python build.py               # 名字和 type 都保留页面原文（繁体）
    python build.py --refresh     # 重新下载 citypopulation 页面
    python build.py --refresh-order   # 重建 .cache/order.json
    python build.py --hk-regions=地區  # 香港岛/九龙/新界 三行改用 地區 作 type
    python build.py --no-hk-regions    # 不补 香港岛/九龙/新界 三行
"""

import argparse
import csv
import json
import sys
from pathlib import Path

# 同目录的通用抓取器：抓页面、解析页面原始字段，不含仓库格式
from fetch import fetch, parse

# 共用的 type 与「专名:通名」在 admin/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from gat_common import SUFFIX, proper_of, split_name, type_out

# huiruisoft 的抓取和解析在 admin/huiruisoft.com/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'huiruisoft.com'))
from huiruisoft import fetch as fetch_huiruisoft, parse as parse_huiruisoft

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / '.cache'
OUTPUT_DIR = ROOT / '.output'
OUT_TAIWAN = OUTPUT_DIR / '台湾.csv'
OUT_HONGKONG = OUTPUT_DIR / '香港.csv'

# 页面最上面那行是国家/特别行政区，本仓库这边是省级。
# 台湾省的名字和 type 按 GB/T 2260（710000 台湾省），香港按页面的
# "Special Administrative Region" -> 特别行政区。parent 是「大区」首位数字，
# 跟 dmfw 的约定一致：7 台湾 8 港澳
TOPS = {
    'taiwan': [1, '7', '71', '省', '臺灣:省'],
    'hongkong': [1, '8', '81', '特别行政区', '香港:特別行政區'],
}

# 香港 18 区没有官方的县级码（GB/T 2260 的香港表暂缺）。这里按通行的那套表补出
# 地级：8101 香港岛、8102 九龙、8103 新界，18 区挂在三个码下面。这三块只是
# 编码表里的地理分组，不是建制单位，默认给 type=區域、名字按仓库格式补通名
# （九龍:區域），和 行政管理区.csv 里带的非建制单位一样。
# --hk-regions=TYPE 可以换成别的 type；--hk-regions= （空）则 type 留空、
# 名字原样不加通名。--no-hk-regions 时不要这三行，18 区直接挂 81，
# 按顺序编成 810101-810118。
HK_REGIONS = [
    ('8101', '香港島'),
    ('8102', '九龍'),
    ('8103', '新界'),
]


def hk_region_rows(type_):
    rows = []
    for code, name in HK_REGIONS:
        suffix = SUFFIX.get(type_, type_)
        rows.append([2, '81', code, type_out(type_),
                     f'{name}:{suffix}' if type_ else name])
    return rows

# 专名 -> 所属 region。区内的编号顺序跟 huiruisoft（见顺序表的 '81/3'），
# 组内从 01 编起。页面原文是繁体，键也按繁体写。
HK_DISTRICTS = {
    '中西': '8101', '灣仔': '8101', '東': '8101', '南': '8101',
    '九龍城': '8102', '油尖旺': '8102', '觀塘': '8102', '黃大仙': '8102',
    '深水埗': '8102', '北': '8103', '大埔': '8103', '沙田': '8103',
    '西貢': '8103', '元朗': '8103', '屯門': '8103', '荃灣': '8103',
    '葵青': '8103', '離島': '8103',
}

PAGES = {
    'taiwan': 'https://www.citypopulation.de/en/taiwan/admin/',
    'hongkong': 'https://www.citypopulation.de/en/china/hongkong/admin/',
}

# 页面的状态标签 -> (本仓库 type, 层级)。层级按本仓库口径：2 地级、3 县级、4 乡级。
# 台湾那三档见 https://www.citypopulation.de/en/taiwan/admin/：直轄市、省轄市、縣；
# 台湾的县下面是县辖市/镇/乡（乡级），市的下面是市辖区（县级）
TYPES = {
    'taiwan': {
        'Special Municipality': ('直辖市', 2),
        # 台湾没有地级市，省辖市就叫「市」；县下面的市是县辖市
        'Provincial City': ('市', 2),
        'County (Xian)': ('县', 3),
        'City District (Qu)': ('市辖区', 3),
        'City (Shi)': ('县辖市', 4),
        'Urban Township (Zhen)': ('镇', 4),
        'Rural Township (Xiang)': ('乡', 4),
    },
    # 香港不是市，18 区是区议会分区，不能用市辖区这个说法
    'hongkong': {
        'District Council': ('区议会', 3),
        'Constituency Area': ('选区', 4),
    },
}

def key_of(parent, level):
    """补号时用的上级 code。

    地级挂省级，上级就是省级本身；香港的 18 区直接挂省级，要补中间两位
    （810101），那两位是空占位。台湾各县的码本来就是 4 位（7122 宜蘭），
    跟直辖市 7101-7109 同一层，不补。其余情况上级 code 就是它自己。
    """
    if level == 3 and len(parent) == 2 and parent != '71':
        return parent + '01'
    return parent


def new_code(parent, level, type_, used):
    """按本仓库的编码规则补号：

        地级 = 省级 + 2 位（71 -> 7101）
        县级 = 上级 + 2 位（7101 -> 710101；台湾的县直接挂 71，71 -> 7122）
        乡级 = 县级 + 3 位，首位是类别标识，见 SERIES（7122 -> 7122100）
    """
    parent = key_of(parent, level)
    if level == 4:
        start, width = SERIES[type_], 3
    else:
        start, width = 1, 2
    for n in range(start, 10 ** width):
        code = f'{parent}{n:0{width}d}'
        if code not in used:
            return code
    raise ValueError(f'{parent} 下号不够用了')


# 乡级码末三位首位是类别标识：街道 0、镇 1、乡 2、类似乡级单位 4-5。台湾、香港这几个
# 按最接近的段放：台湾的县辖市跟镇同段（都是市镇，标准建制序列里没有单给「市」的
# 位子，两类共用一段号、靠 type 分），香港的选区放 0 段
SERIES = {'县辖市': 100, '镇': 100, '乡': 200, '选区': 1}

# 同级单位的顺序取自 huiruisoft 的区划表（https://www.huiruisoft.com/district/），
# 顺序表缓存在 .cache/order.json，由 --refresh-order 抓下来重建，不写死在源码里
# （huiruisoft 页面的缓存在 ../huiruisoft.com/.cache/）。
# 结构是 {"上级code/层级": [{"code": ..., "name": ..., "page": ..., "simple": ...}, ...]}：
#   name    页面原文的繁体专名，跟 unit['proper'] 对得上
#   page    页面上的原名（新竹市/新竹县这种专名相同的靠它区分）
#   code    照抄的码（台湾的 7101/7122、市辖区 710101…），空串表示只借顺序
#   simple  huiruisoft 上的简体全名，刷新时先按它认旧条目，省掉繁简转换
# 表里没有的单位（站点还没有的新单位）排在最后，按页面顺序补号。
ORDER_PATH = CACHE_DIR / 'order.json'

ORDER = None


def huiruisoft_rows(page, refresh):
    """huiruisoft 的区划页 -> [(code, 简体全名)]，站点顺序"""
    rows = parse_huiruisoft(fetch_huiruisoft(page, refresh))
    return [(row['区划代码'], row['中文名称']) for row in rows
            if row.get('区划代码', '').isdigit()]


# 页面最上面那行是国家和地区本身，不是下辖单位
TOP_STATUSES = {'Republic', 'Special Administrative Region'}


def page_rows(page):
    """页面的 [上级原名, 本名, 状态]，跳过最上面那行国家/特别行政区。

    update.parse 给的是英文名，这里换成母语名——huiruisoft 和顺序表都用母语名。
    父行的英文名在 parsed 里先出现，所以能查回母语名。
    """
    parsed = parse(fetch(PAGES[page], ARGS.refresh))
    # 只有组行（parent 空）的英文名才是父名；下级单位可能跟父行重名
    # （苗栗縣和苗栗市英文都叫 Miaoli），不能拿全部行来建表
    native_of = {row['name']: row['native'] for row in parsed if not row['parent']}
    rows = []
    for row in parsed:
        if row['status'] in TOP_STATUSES:
            continue
        parent_name = native_of.get(row['parent']) if row['parent'] else None
        rows.append((parent_name, row['native'], row['status']))
    return rows


def level_of(page, status):
    """页面的状态标签 -> (本仓库 type, 层级)，标签不认识就直接报错"""
    if status not in TYPES[page]:
        raise ValueError(f'{page}: 页面出现了没见过的状态标签 {status!r}')
    return TYPES[page][status]


def page_units():
    """citypopulation 页面上的全部单位，刷新顺序表时用来找繁体名"""
    units = []
    for page in ('taiwan', 'hongkong'):
        for parent_name, name, status in page_rows(page):
            type_, level = level_of(page, status)
            full = split_name(name, type_)
            units.append({'page': page, 'parent': parent_name, 'name': name,
                          'type': type_, 'level': level, 'full': full,
                          'proper': proper_of(full)})
    return units


# 页面用繁体，huiruisoft 用简体，建表时要先把页面名转成简体来对。只需要处理两边
# 写法不同的字：页面上的名字一共用了 557 个字，有差异的 167 个都在下面两行里，
# 是按 zhconv 的 zh-cn 逐字对照出来的（逐个核对过，拼回去跟 zhconv 的结果一致）。
# 有了这张表就不用装 zhconv；哪天页面上冒出新字，转换对不上会按「找不到对应单位」
# 警告出来，把那几个字补进表里即可。
T2S_KEYS = (
    '來偉內勢匯區協員啟國圍園堅堯場壇壘壢壯壽奧學寧寶將岡島峽崗崙嵐嶺嶼巒庫廈廟廣強'
    '彌後徑復恆悅愛掃晉曉東栢棲楊業榮樂樹橋橫櫻歸滿漁漢澤濃濤濱瀝灝灣烏營獅瑪環瓊發'
    '盤硤窩籬紅結統綉綠維線縣羅義聖聯腳臺興舊莊華萬蓮蔭藍蘆蘇蘭衛裡觀計訪詩豐貢貴車'
    '軍軒輋連運達邊鄉鄭銅錦鎮鑼長門間閣關雙離雲霧靈頂順頌頓頭頸顯風館馬駿魚鯉鰂鳥鳳'
    '鴨鵝鵬鶯鶴鹽麗麥黃龍龜'
)
T2S_VALS = (
    '来伟内势汇区协员启国围园坚尧场坛垒坜壮寿奥学宁宝将冈岛峡岗仑岚岭屿峦库厦庙广强'
    '弥后径复恒悦爱扫晋晓东柏栖杨业荣乐树桥横樱归满渔汉泽浓涛滨沥灏湾乌营狮玛环琼发'
    '盘硖窝篱红结统绣绿维线县罗义圣联脚台兴旧庄华万莲荫蓝芦苏兰卫里观计访诗丰贡贵车'
    '军轩𪨶连运达边乡郑铜锦镇锣长门间阁关双离云雾灵顶顺颂顿头颈显风馆马骏鱼鲤鲗鸟凤'
    '鸭鹅鹏莺鹤盐丽麦黄龙龟'
)
assert len(T2S_KEYS) == len(T2S_VALS)
T2S = dict(zip(T2S_KEYS, T2S_VALS))


def to_simplified(text):
    """繁 -> 简，只给重建顺序表做对比用（对照表见上，表外的字原样保留）"""
    return ''.join(T2S.get(ch, ch) for ch in text)


def _unit_simple(unit):
    """页面单位在 huiruisoft 那边的简体写法：全名去掉标注冒号再转简体"""
    return to_simplified(unit['full'].replace(':', ''))


def match_rows(rows, units, old):
    """把一组 huiruisoft 行对到页面单位上，返回 ([(code, simple, unit)], unmatched)。

    rows   [(huiruisoft code, huiruisoft 简体全名)]，站点顺序
    units  这一组页面上的单位
    old    旧表里的全部条目，认得 simple 就不用繁简转换
    """
    # 按页面原名认，不按专名：新竹市和新竹县的专名都是「新竹」，专名会撞
    by_page = {u['name']: u for u in units}
    by_simple = {e['simple']: e['page'] for e in old if e.get('page') in by_page}
    page_simple = None
    matched, unmatched = [], []
    for code, simple in rows:
        page = by_simple.get(simple)
        if page not in by_page:
            if page_simple is None:
                page_simple = {}
                for unit in units:
                    page_simple.setdefault(_unit_simple(unit), unit['name'])
            page = page_simple.get(simple)
        if page not in by_page:
            unmatched.append(simple)
            continue
        matched.append((code, simple, by_page[page]))
    return matched, unmatched


def fetch_order(refresh=False):
    """从 huiruisoft 重建顺序表，写到 .cache/order.json。

    refresh=True 时重下 huiruisoft 的页面；citypopulation 的页面照 --refresh 的缓存走。
    返回表本身（和落盘的内容一致）。
    """
    units = page_units()
    old = {}
    if ORDER_PATH.exists():
        try:
            old = json.loads(ORDER_PATH.read_text(encoding='utf-8'))
        except ValueError:
            print(f'警告：{ORDER_PATH} 读不了，按没有旧表处理', file=sys.stderr)
    old_entries = [entry for entries in old.values() for entry in entries]

    order = {}
    top = {}
    for page, page_code in (('taiwan', '710000'), ('hongkong', '810000')):
        page_top = [u for u in units if u['page'] == page and u['parent'] is None]
        rows = huiruisoft_rows(page_code, refresh)
        matched, unmatched = match_rows(rows, page_top, old_entries)
        top[page] = matched
        for code, simple, unit in matched:
            group = f'{TOPS_CODE[page]}/{unit["level"]}'
            order.setdefault(group, []).append({
                'code': code[:4] if page == 'taiwan' else '',
                'name': unit['proper'],
                'page': unit['name'],
                'simple': simple,
            })
        for simple in unmatched:
            print(f'警告：huiruisoft {page_code} 的 {simple} 在页面上找不到对应单位，跳过',
                  file=sys.stderr)

    # 台湾各市县的下级页：市下面是区（县级），县下面是乡镇（乡级）
    for code, _, parent_unit in top['taiwan']:
        prefix = code[:4]
        children = [u for u in units
                    if u['page'] == 'taiwan' and u['parent'] == parent_unit['name']]
        if not children:
            print(f'警告：{prefix} {parent_unit["name"]} 页面上没有下级单位，跳过',
                  file=sys.stderr)
            continue
        levels = sorted({u['level'] for u in children})
        if len(levels) != 1:
            raise ValueError(f'{prefix} 的下级层级不统一：{levels}')
        level = levels[0]
        rows = huiruisoft_rows(f'71/{prefix}00', refresh)
        matched, unmatched = match_rows(rows, children, old_entries)
        group = f'{prefix}/{level}'
        for huiruisoft_code, simple, child in matched:
            order.setdefault(group, []).append({
                # 市辖区照抄 huiruisoft 的 6 位码；乡镇只借顺序，码按本仓库规则现编
                'code': huiruisoft_code if parent_unit['level'] == 2 else '',
                'name': child['proper'],
                'page': child['name'],
                'simple': simple,
            })
        for simple in unmatched:
            print(f'警告：huiruisoft 71/{prefix}00 的 {simple} 在页面上找不到对应单位，跳过',
                  file=sys.stderr)

    ORDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORDER_PATH.write_text(json.dumps(order, ensure_ascii=False) + '\n',
                          encoding='utf-8', newline='\n')
    print(f'顺序表 {sum(map(len, order.values()))} 条，{len(order)} 组 -> {ORDER_PATH}',
          file=sys.stderr)
    return order


def load_order():
    """读顺序表；没有就先抓一次（见 fetch_order）"""
    global ORDER
    if ORDER is None:
        if ORDER_PATH.exists():
            ORDER = json.loads(ORDER_PATH.read_text(encoding='utf-8'))
        else:
            print(f'{ORDER_PATH} 不存在，先抓一次 huiruisoft 的顺序表', file=sys.stderr)
            ORDER = fetch_order()
    return ORDER


def ordered(page, units, parent, level):
    """按顺序表排一组同级单位，带出表里的号。

    返回 [(unit, code)]。表里对得上的按站点顺序排前面，code 是表里写的
    （'' 表示只借顺序）；对不上的按页面顺序排在最后，code 为 None。
    有表可查时，两边对不上（表里没有的单位、页面上没有的单位）都打警告。
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
            print(f'警告：{page} {parent}/{level} 的 {unit["full"]} 不在'
                  f'顺序表里，排到最后按本仓库规则补号', file=sys.stderr)
            tagged.append((len(order), 1, i, unit, None))
    for entry in order:
        if entry['name'] not in seen:
            print(f'警告：顺序表 {parent}/{level} 里的 {entry["name"]} '
                  f'页面上没有，跳过', file=sys.stderr)
    tagged.sort(key=lambda item: item[:3])
    return [(unit, code) for *_, unit, code in tagged]


def build(page, used):
    """把一页的页面数据变成 [level, parent, code, type, name]"""
    parsed = []
    for parent_name, name, status in page_rows(page):
        type_, level = level_of(page, status)
        full = split_name(name, type_)
        parsed.append({'parent_name': parent_name, 'name': name, 'type': type_,
                       'level': level, 'full': full, 'proper': proper_of(full)})

    # 按页面顺序分组；父行总在子行前面，所以父的 code 先算好
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
        # 页面第二级的上级：台湾的市县都直接挂 71（县是省直辖县级），
        # 香港挂 81
        parent = TOPS_CODE[page] if parent_name is None else code_of[parent_name]
        for level in sorted({unit['level'] for unit in units}):
            same_level = [u for u in units if u['level'] == level]
            if page == 'hongkong' and parent_name is None and not ARGS.no_hk_regions:
                # 18 区先按 huiruisoft 的顺序排，再按 region 分组，组内从 01 编起：
                # 810101-810104 香港岛、810201-810205 九龙、810301-810309 新界
                counters = {}
                for unit, _ in ordered(page, same_level, TOPS_CODE[page], level):
                    region = HK_DISTRICTS[unit['proper']]
                    n = counters[region] = counters.get(region, 0) + 1
                    code = f'{region}{n:02d}'
                    code_of[unit['name']] = code
                    used.add(code)
                    rows.append([level, region, code, type_out(unit['type']),
                                 unit['full']])
                continue
            for unit, code in ordered(page, same_level, parent, level):
                if code is None:
                    code = new_code(parent, level, unit['type'], used)
                code_of[unit['name']] = code
                used.add(code)
                rows.append([level, parent, code, type_out(unit['type']),
                             unit['full']])
    return rows


TOPS_CODE = {'taiwan': '71', 'hongkong': '81'}


def top_rows(page):
    """页面最上面那行（臺灣省、香港特别行政区）的仓库格式行"""
    level, parent, code, type_, name = TOPS[page]
    return [[level, parent, code, type_out(type_), name]]


def write_table(rows, path):
    """排好序、查重后写一张表"""
    # 同一张表里 code 撞车只可能是补号逻辑坏了（页面上的名字在上级内唯一）
    # 早点炸掉比悄悄出一份重码的表强
    seen = {}
    for level, parent, code, type_, name in rows:
        if code in seen:
            raise ValueError(f'{path.name} 里 {code} 撞车：{seen[code]} / {name}')
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
    print(f'{len(rows)} 条 -> {path}', file=sys.stderr)
    for (level, type_), n in sorted(counts.items()):
        print(f'  {level} 级 {type_ or "(无)"}: {n}', file=sys.stderr)


def main():
    if ARGS.refresh_order:
        fetch_order(refresh=True)

    used = set()
    rows = top_rows('taiwan')
    used |= {row[2] for row in rows}
    rows += build('taiwan', used)
    write_table(rows, OUT_TAIWAN)

    used = set()
    rows = top_rows('hongkong')
    if not ARGS.no_hk_regions:
        rows += hk_region_rows(ARGS.hk_regions)
    used |= {row[2] for row in rows}
    rows += build('hongkong', used)
    write_table(rows, OUT_HONGKONG)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='抓 citypopulation.de 生成台湾、香港表')
    ap.add_argument('--refresh', action='store_true', help='重新下载页面，不用缓存')
    ap.add_argument('--refresh-order', action='store_true',
                    help='重新下载 huiruisoft 的区划表，重建 .cache/order.json')
    ap.add_argument('--hk-regions', nargs='?', const='', default='区域', metavar='TYPE',
                    help='补 香港岛/九龙/新界 三行，18 区挂到 8101/8102/8103 下；'
                         '默认 type=區域（九龍:區域），--hk-regions=TYPE 换 type，'
                         '--hk-regions= （空）则 type 留空、名字不加通名')
    ap.add_argument('--no-hk-regions', action='store_true',
                    help='不补这三行：18 区直接挂 81，按顺序编成 810101-810118')
    ARGS = ap.parse_args()
    main()
