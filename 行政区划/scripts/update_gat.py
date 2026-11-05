#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 .output/港澳台.csv：台湾、香港抓 citypopulation.de，澳门按 DSEC 统计分区。

页面缓存在 .cache/citypopulation/ 和 .cache/huiruisoft/（--refresh 时重下），不跟输出混在一起。

数据来源：
    台湾  https://www.citypopulation.de/en/taiwan/admin/
    香港  https://www.citypopulation.de/en/china/hongkong/admin/
    澳门  DSEC《統計年鑑二零二五年》表 1.1「統計分區」的 26 个分区，编号和名字抄在
          源码的 MO_UNITS 里。不读 citypopulation 的澳门页：那张表只有 24 个分区
          （少了 路氹填海區、新城C區），还把新城A區单列成一个 Zone。

页面给的是英文状态标签和繁体名，读进来做三件事：
    状态标签 -> type（Special Municipality -> 直轄市，Parish -> 堂區…）。
    type 不跟大陆的行政区划分类对齐（台湾没有地级市，省辖市就是「市」，县下是「縣轄市」），
    跟名字一样照页面原文写繁体
    名字保留页面原文（繁体）
    名字切「专名:通名」，通名由 type 定；页面没写通名的补上（澳门 氹仔 -> 氹仔:堂區）

台湾、香港的顺序和码值取自 huiruisoft 的区划表，表缓存在
.cache/huiruisoft/order.json，由 --refresh-order 从 https://www.huiruisoft.com/district/
抓下来重建，不写在源码里（澳门不查这张表，见下）。
表里给了码的照抄：台湾的直辖市/省辖市 7101-7109、县 7122/7124/…（那边跳号这边也跳，
不加 00 尾巴）、台南的区跳过 710503/710505。表里只有顺序没有码的（台湾乡级、港澳），
按本仓库规则现编：

    地级 = 省级 + 2 位（71 -> 7101）
    县级 = 上级 + 2 位（7101 -> 710101）；香港澳门直接挂省级，补中间两位
           （81 -> 810101，82 -> 820101）；台湾的县跟直辖市同为 4 位（71 -> 7122）
    乡级 = 县级 + 3 位，首位按类别分段（见 SERIES；7122 -> 7122100）

页面（citypopulation.de）只提供单位和名字；表里对不上或不认识的单位排在同级最后，
按页面顺序补号，并往 stderr 打一条警告。建表时用源码里的繁简对照表把页面名转成
简体跟 huiruisoft 对（只收页面用到的差异字，不需要装 zhconv）；表建好以后刷新先按
旧条目里的简体名认，平时跑根本不转。
香港的 18 区按 HK_DISTRICTS 挂到 8101 香港島 / 8102 九龍 / 8103 新界
下面，各 region 内再按 huiruisoft 的顺序从 01 编起。这三行不是建制单位，默认
type=區域、名字按仓库格式补通名（2,81,8102,區域,九龍:區域）；
--hk-regions=TYPE 可以换成别的 type，--hk-regions= （空）则 type 留空、名字不加通名。
用 --no-hk-regions 可以不要这三行：18 区直接挂 81，按 huiruisoft 顺序编成
810101-810118。
澳门同理：7 个堂区和 3 个不属堂区的新城区挂到 8201 澳門半島 / 8202 氹仔 /
8203 路環 / 8204 路氹城 下面（分组见 MO_UNITS），组内从 01 编起。澳门没有这一级
行政区，这四行也是补出来的地理分组。这几行默认同样 type=區域、
名字补通名（2,82,8201,區域,澳門半島:區域）；--mo-regions=TYPE 换 type，
--mo-regions= （空）则 type 留空。用 --no-mo-regions 可以不要这四行：
10 个县级单位直接挂 82，编成 820101-820110。

用法:
    python update_gat.py               # 名字和 type 都保留页面原文（繁体）
    python update_gat.py --refresh     # 重新下载 citypopulation 页面
    python update_gat.py --refresh-order   # 重建 .cache/huiruisoft/order.json
    python update_gat.py --hk-regions=地區  # 香港岛/九龙/新界 三行改用 地區 作 type
    python update_gat.py --no-hk-regions    # 不补 香港岛/九龙/新界 三行
    python update_gat.py --no-mo-regions    # 不补 澳門半島/氹仔/路環/路氹城 四行
"""

import argparse
import csv
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / '.cache'
# 两个来源的页面分开缓存，别混在一层
CP_DIR = CACHE_DIR / 'citypopulation'
huiruisoft_DIR = CACHE_DIR / 'huiruisoft'
OUTPUT_DIR = ROOT / '.output'
OUT = OUTPUT_DIR / '港澳台.csv'

# 页面最上面那行是国家/特别行政区，本仓库这边是省级。
# 台湾省的名字和 type 按 GB/T 2260（710000 台湾省），香港澳门两个按页面的
# "Special Administrative Region" -> 特别行政区。parent 是「大区」首位数字，
# 跟 update.py 的约定一致：7 台湾 8 港澳
TOPS = [
    [1, '7', '71', '省', '臺灣:省'],
    [1, '8', '81', '特别行政区', '香港:特別行政區'],
    [1, '8', '82', '特别行政区', '澳門:特別行政區'],
]

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

# 澳门在特别行政区和堂区之间没有一级行政区，这四行跟香港的三行一样是编码表里的
# 地理分组：8201 澳門半島、8202 氹仔、8203 路環、8204 路氹城。
# --mo-regions=TYPE 换 type，--mo-regions= （空）则 type 留空、名字不加通名；
# --no-mo-regions 时不要这四行，10 个县级单位直接挂 82，编成 820101-820110。
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

PAGES = {
    'taiwan': 'https://www.citypopulation.de/en/taiwan/admin/',
    'hongkong': 'https://www.citypopulation.de/en/china/hongkong/admin/',
    'macau': 'https://www.citypopulation.de/en/china/macau/admin/',
}

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

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
    'macau': {
        'Parish': ('堂区', 3),
        # 页面把新城A区单列一行，标签是 Zone，跟 7 个堂区并列，所以也算县级
        'Zone': ('区', 3),
        'Statistical District': ('统计区', 4),
    },
}

# 港澳台的 type 不跟大陆的行政区划分类对齐，照页面原文的繁体写。
# TYPES 里统一用简体键，输出前查这张表换成繁体。
TRAD_TYPE = {
    '省': '省', '特别行政区': '特別行政區',
    '直辖市': '直轄市', '县': '縣', '市辖区': '市轄區',
    '市': '市', '县辖市': '縣轄市', '镇': '鎮', '乡': '鄉',
    '区议会': '區議會', '选区': '選區',
    '堂区': '堂區', '区': '區', '统计区': '統計區', '区域': '區域',
}


def type_out(type_):
    """输出用的 type：统一换成页面原文的繁体；表里没有的自定义 type 原样"""
    return TRAD_TYPE.get(type_, type_)


# type -> 名字末尾那个通名。None 表示这类单位名字里不带通名，原样留着（香港的选区
# 是「宝翠」「卫城」这种，没有通名可切）。页面原文是繁体，通名也按繁体比。
SUFFIX = {
    '省': '省', '特别行政区': '特別行政區',
    '直辖市': '市', '县': '縣', '市辖区': '區',
    '市': '市', '县辖市': '市', '镇': '鎮', '乡': '鄉', '区域': '區域',
    '区议会': '區', '选区': None,
    '堂区': '堂區', '区': '區', '统计区': '統計區',
}

# 这两处名字里没有通名或通名写残了，切之前先补回来：
#   堂区  页面把「堂區」写成「堂」（花地瑪堂、聖安多尼堂…），先补掉那个堂。
#         「大堂」不补——剥了只剩一个「大」字，那个堂是名字的一部分
#   区议会  页面漏了「深水埗」的「區」（其余 17 个都有），按通名补上
def split_name(name, type_):
    """把页面名字切成本仓库的「专名:通名」"""
    suffix = SUFFIX[type_]
    if suffix is None:
        return name
    if type_ == '堂区' and name.endswith('堂') and len(name) >= 3:
        name = name[:-1]
    if name.endswith(suffix) and len(name) > len(suffix):
        return f'{name[:-len(suffix)]}:{suffix}'
    return f'{name}:{suffix}'


def proper_of(name):
    """取「专名:通名」里的专名"""
    return name.split(':')[0]


ROW_RE = re.compile(r'<tr[^>]*>.*?</tr>', re.S)
STATUS_RE = re.compile(r'class="rstatus">(.*?)</td>')
NATIVE_RE = re.compile(r'class="rnative"><span itemprop="name">(.*?)</span>')


def parse(html):
    """页面是一张平表，组行（<tr class="rname">）是第二级，它下面到下一个组行
    之前的都是第三级。返回 [(父名, 本名, 状态)]，父名统一是第二级的名字
    （两个特别行政区的第二级就是最底下那级，页面不再往下分了）"""
    rows = []
    parent = None
    for tr in ROW_RE.findall(html):
        native = NATIVE_RE.search(tr)
        if not native:
            continue
        status = STATUS_RE.search(tr)
        status = status.group(1) if status else ''
        # 表尾那行是国家/特别行政区本身，不是下辖单位
        if status in ('Republic', 'Special Administrative Region'):
            continue
        # 页面的「路環」用的是兼容汉字 U+F937，NFC 归一化回 U+8DEF，
        # 同一处地名在表里就有两种码点写法（看着一模一样，查起来对不上）
        name = unicodedata.normalize('NFC', native.group(1))
        if 'class="rname"' in re.search(r'<tr([^>]*)>', tr).group(1):
            parent = name
            rows.append((None, name, status))
        else:
            rows.append((parent, name, status))
    return rows


def fetch(page, refresh):
    """下载并缓存，缓存是为了不每跑一次都打人家的站"""
    cache = CP_DIR / (page + '.html')
    if cache.exists() and not refresh:
        return cache.read_text(encoding='utf-8')
    print(f'下载 {PAGES[page]}', file=sys.stderr)
    req = urllib.request.Request(PAGES[page], headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode('utf-8')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding='utf-8', newline='\n')
    return html


def key_of(parent, level):
    """补号时用的上级 code。

    地级挂省级，上级就是省级本身；香港澳门的 18 区、7 个堂区直接挂省级，要补
    中间两位（810101、820101），那两位是空占位。台湾各县的码本来就是 4 位
    （7122 宜蘭），跟直辖市 7101-7109 同一层，不补。
    其余情况上级 code 就是它自己。
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


# 乡级码末三位首位是类别标识：街道 0、镇 1、乡 2、类似乡级单位 4-5。港澳台这几个
# 按最接近的段放：台湾的县辖市跟镇同段（都是市镇，标准建制序列里没有单给「市」的
# 位子，两类共用一段号、靠 type 分），香港的选区、澳门的统计区放 0 段
SERIES = {'县辖市': 100, '镇': 100, '乡': 200, '选区': 1, '统计区': 1}

# 同级单位的顺序取自 huiruisoft 的区划表（https://www.huiruisoft.com/district/），
# 表缓存在 .cache/huiruisoft/huiruisoft.json，由 --refresh-order 抓下来重建，不写死在源码里。
# 结构是 {"上级code/层级": [{"code": ..., "name": ..., "page": ..., "simple": ...}, ...]}：
#   name    页面原文的繁体专名，跟 unit['proper'] 对得上
#   page    页面上的原名（新竹市/新竹县这种专名相同的靠它区分）
#   code    照抄的码（台湾的 7101/7122、市辖区 710101…），空串表示只借顺序
#   simple  huiruisoft 上的简体全名，刷新时先按它认旧条目，省掉繁简转换
# 表里没有的单位（站点还没有的新单位）排在最后，按页面顺序补号。
ORDER_PATH = huiruisoft_DIR / 'order.json'
huiruisoft_BASE = 'https://www.huiruisoft.com/district/'

# huiruisoft 跟 citypopulation 对不上的几处，键是 (上级 code, 层级)，值是
# 简体全名 -> 页面原名。澳门 huiruisoft 用堂区的本名，页面用所在的地名；
# 路氹城是填海区，页面上没有对应单位（新城A区是另一块），跳过。
huiruisoft_ALIASES = {
    ('82', 3): {'大堂区': '大堂', '嘉模堂区': '氹仔', '圣方济各堂区': '路環'},
}
huiruisoft_SKIP = {('82', 3): {'路氹城'}}

huiruisoft_TABLE_RE = re.compile(r'<table[^>]*class="table-divisions"[^>]*>(.*?)</table>', re.S)
TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
TAG_RE = re.compile(r'<[^>]+>')

ORDER = None


def fetch_huiruisoft(page, refresh):
    """抓 huiruisoft 的区划页，page 形如 710000 或 71/710100"""
    cache = huiruisoft_DIR / (page + '.html')
    if cache.exists() and not refresh:
        return cache.read_text(encoding='utf-8')
    url = f'{huiruisoft_BASE}{page}.html'
    print(f'下载 {url}', file=sys.stderr)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode('utf-8')
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(html, encoding='utf-8', newline='\n')
    return html


def parse_huiruisoft(html):
    """huiruisoft 的区划表。每行是 [code, 名称, 邮编, 区号, 面积, 驻地]，只取前两列"""
    table = huiruisoft_TABLE_RE.search(html)
    if not table:
        return []
    rows = []
    for tr in ROW_RE.findall(table.group(1)):
        cells = [TAG_RE.sub('', cell).strip() for cell in TD_RE.findall(tr)]
        if len(cells) >= 2 and cells[0].isdigit():
            rows.append((cells[0], cells[1]))
    return rows


def page_units():
    """citypopulation 页面上的全部单位，刷新顺序表时用来找繁体名"""
    units = []
    for page in ('taiwan', 'hongkong', 'macau'):
        for parent_name, name, status in parse(fetch(page, ARGS.refresh)):
            if status not in TYPES[page]:
                raise ValueError(f'{page}: 页面出现了没见过的状态标签 {status!r}')
            type_, level = TYPES[page][status]
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


def match_rows(rows, units, old, key):
    """把一组 huiruisoft 行对到页面单位上，返回 ([(code, simple, unit)], unmatched)。

    rows   [(huiruisoft code, huiruisoft 简体全名)]，站点顺序
    units  这一组页面上的单位
    old    旧表里的全部条目，认得 simple 就不用繁简转换
    key    (上级 code, 层级)，查别名表
    """
    aliases = huiruisoft_ALIASES.get(key, {})
    skip = huiruisoft_SKIP.get(key, set())
    # 按页面原名认，不按专名：新竹市和新竹县的专名都是「新竹」，专名会撞
    by_page = {u['name']: u for u in units}
    by_simple = {e['simple']: e['page'] for e in old if e.get('page') in by_page}
    page_simple = None
    matched, unmatched = [], []
    for code, simple in rows:
        if simple in skip:
            continue
        page = aliases.get(simple)
        if page not in by_page:
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
    """从 huiruisoft 重建顺序表，写到 .cache/huiruisoft/huiruisoft.json。

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
    for page, page_code in (('taiwan', '710000'), ('hongkong', '810000'), ('macau', '820000')):
        page_top = [u for u in units if u['page'] == page and u['parent'] is None]
        rows = parse_huiruisoft(fetch_huiruisoft(page_code, refresh))
        matched, unmatched = match_rows(rows, page_top, old_entries, (TOPS_CODE[page], 3))
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
        rows = parse_huiruisoft(fetch_huiruisoft(f'71/{prefix}00', refresh))
        matched, unmatched = match_rows(rows, children, old_entries, (prefix, level))
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
    for parent_name, name, status in parse(fetch(page, ARGS.refresh)):
        if status not in TYPES[page]:
            raise ValueError(f'{page}: 页面出现了没见过的状态标签 {status!r}')
        type_, level = TYPES[page][status]
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
        # 香港澳门挂 81/82
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


TOPS_CODE = {'taiwan': '71', 'hongkong': '81', 'macau': '82'}


def main():
    if ARGS.refresh_order:
        fetch_order(refresh=True)
    used = set()
    fixed = [[level, parent, code, type_out(type_), name]
             for level, parent, code, type_, name in TOPS]
    if not ARGS.no_hk_regions:
        fixed += hk_region_rows(ARGS.hk_regions)
    if not ARGS.no_mo_regions:
        fixed += mo_region_rows(ARGS.mo_regions)
    rows = list(fixed)
    used |= set(row[2] for row in fixed)
    for page in ('taiwan', 'hongkong'):
        rows.extend(build(page, used))
    rows.extend(mo_rows(used))

    # 同一张表里 code 撞车只可能是补号逻辑坏了（页面上的名字在上级内唯一）
    # 早点炸掉比悄悄出一份重码的表强
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
    ap = argparse.ArgumentParser(description='抓 citypopulation.de 生成港澳台源表')
    ap.add_argument('--refresh', action='store_true', help='重新下载页面，不用缓存')
    ap.add_argument('--refresh-order', action='store_true',
                    help='重新下载 huiruisoft 的区划表，重建 .cache/huiruisoft/huiruisoft.json')
    ap.add_argument('--hk-regions', nargs='?', const='', default='区域', metavar='TYPE',
                    help='补 香港岛/九龙/新界 三行，18 区挂到 8101/8102/8103 下；'
                         '默认 type=區域（九龍:區域），--hk-regions=TYPE 换 type，'
                         '--hk-regions= （空）则 type 留空、名字不加通名')
    ap.add_argument('--no-hk-regions', action='store_true',
                    help='不补这三行：18 区直接挂 81，按顺序编成 810101-810118')
    ap.add_argument('--mo-regions', nargs='?', const='', default='区域', metavar='TYPE',
                    help='补 澳門半島/氹仔/路環/路氹城 四行，堂区与三个新城区挂到 '
                         '8201-8204 下；默认 type=區域（澳門半島:區域），'
                         '--mo-regions=TYPE 换 type，--mo-regions= （空）则 type 留空、'
                         '名字不加通名')
    ap.add_argument('--no-mo-regions', action='store_true',
                    help='不补这四行：10 个县级单位直接挂 82，编成 820101-820110')
    ARGS = ap.parse_args()
    main()
