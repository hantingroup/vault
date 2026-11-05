#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓 citypopulation.de 的港澳台三份区划表，生成 .output/港澳台.csv。

页面缓存在 .cache/（--refresh 时重下），不跟输出混在一起。

本仓库的港澳台只到省级，下面各级一律以 citypopulation.de 为准：
    台湾  https://www.citypopulation.de/en/taiwan/admin/
    香港  https://www.citypopulation.de/en/china/hongkong/admin/
    澳门  https://www.citypopulation.de/en/china/macau/admin/

页面给的是英文状态标签和繁体名，读进来做三件事：
    状态标签 -> type（Special Municipality -> 直轄市，Parish -> 堂區…）。
    type 不跟大陆的行政区划分类对齐（台湾没有地级市，省辖市就是「市」，县下是「縣轄市」），
    跟名字一样照页面原文写繁体
    名字保留页面原文（繁体）
    名字切「专名:通名」，通名由 type 定；页面没写通名的补上（澳门 氹仔 -> 氹仔:堂區）

顺序和码值取自 huiruisoft 的区划表（见 HUIRUI_ORDER）。表里给了码的照抄：台湾的
直辖市/省辖市 7101-7109、县 7122/7124/…（那边跳号这边也跳，不加 00 尾巴）、
台南的区跳过 710503/710505。表里只有顺序没有码的（台湾乡级、港澳），按本仓库规则现编：

    地级 = 省级 + 2 位（71 -> 7101）
    县级 = 上级 + 2 位（7101 -> 710101）；香港澳门直接挂省级，补中间两位
           （81 -> 810101，82 -> 820101）；台湾的县跟直辖市同为 4 位（71 -> 7122）
    乡级 = 县级 + 3 位，首位按类别分段（见 SERIES；7122 -> 7122100）

页面（citypopulation.de）只提供单位和名字；表里对不上或不认识的单位排在同级最后，
按页面顺序补号，并往 stderr 打一条警告。
香港的 18 区按 HK_DISTRICTS 挂到 8101 香港島 / 8102 九龍 / 8103 新界
下面，各 region 内再按 huiruisoft 的顺序从 01 编起。这三行不是建制单位，默认
type=區域、名字按仓库格式补通名（2,81,8102,區域,九龍:區域）；
--hk-regions=TYPE 可以换成别的 type，--hk-regions= （空）则 type 留空、名字不加通名。
用 --no-hk-regions 可以不要这三行：18 区直接挂 81，按 huiruisoft 顺序编成
810101-810118。

用法:
    python update_gat.py               # 名字和 type 都保留页面原文（繁体）
    python update_gat.py --refresh     # 重新下载
    python update_gat.py --hk-regions=地區  # 三行改用 地區 作 type
    python update_gat.py --no-hk-regions    # 不补 香港岛/九龙/新界 三行
"""

import argparse
import csv
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / '.cache'
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

# 专名 -> 所属 region。区内的编号顺序跟 huiruisoft（见 HUIRUI_ORDER 的 ('81', 3)），
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
    cache = CACHE_DIR / (page + '.html')
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

# 同级单位的顺序取自 huiruisoft（https://www.huiruisoft.com/district/，
# 台湾 710000.html、香港 810000.html、澳门 820000.html 及各下级页）。
# 键是 (上级 code, 层级)，值是 [(专名, code)]，专名是页面原文的繁体。
#   - code 非空：码也照它来。台湾的县（7122/7124…）、台南的区（710503、
#     710505 空着）在那边是跳号的，这里跟着跳，不补。
#   - code 为空：只借顺序，码仍按本仓库规则现编（台湾的乡级、港澳）。
# 表里没有的单位（站点还没有的新单位）排在最后，按页面顺序补号。
HUIRUI_ORDER = {
    ('71', 2): [
        ('臺北', '7101'), ('高雄', '7102'), ('基隆', '7103'), ('臺中', '7104'), ('臺南', '7105'),
        ('新竹', '7106'), ('嘉義', '7107'), ('新北', '7108'), ('桃園', '7109'),
    ],
    ('71', 3): [
        ('宜蘭', '7122'), ('新竹', '7124'), ('苗栗', '7125'), ('彰化', '7127'),
        ('南投', '7128'), ('雲林', '7129'), ('嘉義', '7130'), ('屏東', '7133'),
        ('臺東', '7134'), ('花蓮', '7135'), ('澎湖', '7136'), ('金門', '7137'),
        ('連江', '7138'),
    ],
    ('7101', 3): [
        ('松山', '710101'), ('信義', '710102'), ('大安', '710103'), ('中山', '710104'),
        ('中正', '710105'), ('大同', '710106'), ('萬華', '710107'), ('文山', '710108'),
        ('南港', '710109'), ('內湖', '710110'), ('士林', '710111'), ('北投', '710112'),
    ],
    ('7102', 3): [
        ('鹽埕', '710201'), ('鼓山', '710202'), ('左營', '710203'), ('楠梓', '710204'),
        ('三民', '710205'), ('新興', '710206'), ('前金', '710207'), ('苓雅', '710208'),
        ('前鎮', '710209'), ('旗津', '710210'), ('小港', '710211'), ('鳳山', '710212'),
        ('林園', '710213'), ('大寮', '710214'), ('大樹', '710215'), ('大社', '710216'),
        ('仁武', '710217'), ('鳥松', '710218'), ('岡山', '710219'), ('橋頭', '710220'),
        ('燕巢', '710221'), ('田寮', '710222'), ('阿蓮', '710223'), ('路竹', '710224'),
        ('湖內', '710225'), ('茄萣', '710226'), ('永安', '710227'), ('彌陀', '710228'),
        ('梓官', '710229'), ('旗山', '710230'), ('美濃', '710231'), ('六龜', '710232'),
        ('甲仙', '710233'), ('杉林', '710234'), ('內門', '710235'), ('茂林', '710236'),
        ('桃源', '710237'), ('那瑪夏', '710238'),
    ],
    ('7103', 3): [
        ('中正', '710301'), ('七堵', '710302'), ('暖暖', '710303'), ('仁愛', '710304'),
        ('中山', '710305'), ('安樂', '710306'), ('信義', '710307'),
    ],
    ('7104', 3): [
        ('中', '710401'), ('東', '710402'), ('南', '710403'), ('西', '710404'),
        ('北', '710405'), ('西屯', '710406'), ('南屯', '710407'), ('北屯', '710408'),
        ('豐原', '710409'), ('東勢', '710410'), ('大甲', '710411'), ('清水', '710412'),
        ('沙鹿', '710413'), ('梧棲', '710414'), ('后里', '710415'), ('神岡', '710416'),
        ('潭子', '710417'), ('大雅', '710418'), ('新社', '710419'), ('石岡', '710420'),
        ('外埔', '710421'), ('大安', '710422'), ('烏日', '710423'), ('大肚', '710424'),
        ('龍井', '710425'), ('霧峰', '710426'), ('太平', '710427'), ('大里', '710428'),
        ('和平', '710429'),
    ],
    ('7105', 3): [
        ('東', '710501'), ('南', '710502'), ('北', '710504'), ('安南', '710506'),
        ('安平', '710507'), ('中西', '710508'), ('新營', '710509'), ('鹽水', '710510'),
        ('白河', '710511'), ('柳營', '710512'), ('後壁', '710513'), ('東山', '710514'),
        ('麻豆', '710515'), ('下營', '710516'), ('六甲', '710517'), ('官田', '710518'),
        ('大內', '710519'), ('佳里', '710520'), ('學甲', '710521'), ('西港', '710522'),
        ('七股', '710523'), ('將軍', '710524'), ('北門', '710525'), ('新化', '710526'),
        ('善化', '710527'), ('新市', '710528'), ('安定', '710529'), ('山上', '710530'),
        ('玉井', '710531'), ('楠西', '710532'), ('南化', '710533'), ('左鎮', '710534'),
        ('仁德', '710535'), ('歸仁', '710536'), ('關廟', '710537'), ('龍崎', '710538'),
        ('永康', '710539'),
    ],
    ('7106', 3): [
        ('東', '710601'), ('北', '710602'), ('香山', '710603'),
    ],
    ('7107', 3): [
        ('東', '710701'), ('西', '710702'),
    ],
    ('7108', 3): [
        ('板橋', '710801'), ('三重', '710802'), ('中和', '710803'), ('永和', '710804'),
        ('新莊', '710805'), ('新店', '710806'), ('樹林', '710807'), ('鶯歌', '710808'),
        ('三峽', '710809'), ('淡水', '710810'), ('汐止', '710811'), ('瑞芳', '710812'),
        ('土城', '710813'), ('蘆洲', '710814'), ('五股', '710815'), ('泰山', '710816'),
        ('林口', '710817'), ('深坑', '710818'), ('石碇', '710819'), ('坪林', '710820'),
        ('三芝', '710821'), ('石門', '710822'), ('八里', '710823'), ('平溪', '710824'),
        ('雙溪', '710825'), ('貢寮', '710826'), ('金山', '710827'), ('萬里', '710828'),
        ('烏來', '710829'),
    ],
    ('7109', 3): [
        ('桃園', '710901'), ('中壢', '710902'), ('平鎮', '710903'), ('八德', '710904'),
        ('楊梅', '710905'), ('大溪', '710906'), ('蘆竹', '710907'), ('大園', '710908'),
        ('龜山', '710909'), ('龍潭', '710910'), ('新屋', '710911'), ('觀音', '710912'),
        ('復興', '710913'),
    ],
    ('7122', 4): [
        ('宜蘭', ''), ('頭城', ''), ('羅東', ''), ('蘇澳', ''), ('礁溪', ''), ('壯圍', ''), ('員山', ''),
        ('冬山', ''), ('五結', ''), ('三星', ''), ('大同', ''), ('南澳', ''),
    ],
    ('7124', 4): [
        ('竹北', ''), ('竹東', ''), ('新埔', ''), ('關西', ''), ('湖口', ''), ('新豐', ''), ('峨眉', ''),
        ('寶山', ''), ('北埔', ''), ('芎林', ''), ('橫山', ''), ('尖石', ''), ('五峰', ''),
    ],
    ('7125', 4): [
        ('苗栗', ''), ('頭份', ''), ('竹南', ''), ('後龍', ''), ('通霄', ''), ('苑裡', ''), ('卓蘭', ''),
        ('造橋', ''), ('西湖', ''), ('頭屋', ''), ('公館', ''), ('銅鑼', ''), ('三義', ''), ('大湖', ''),
        ('獅潭', ''), ('三灣', ''), ('南庄', ''), ('泰安', ''),
    ],
    ('7127', 4): [
        ('彰化', ''), ('員林', ''), ('和美', ''), ('鹿港', ''), ('溪湖', ''), ('二林', ''), ('田中', ''),
        ('北斗', ''), ('花壇', ''), ('芬園', ''), ('大村', ''), ('永靖', ''), ('伸港', ''), ('線西', ''),
        ('福興', ''), ('秀水', ''), ('埔心', ''), ('埔鹽', ''), ('大城', ''), ('芳苑', ''), ('竹塘', ''),
        ('社頭', ''), ('二水', ''), ('田尾', ''), ('埤頭', ''), ('溪州', ''),
    ],
    ('7128', 4): [
        ('南投', ''), ('埔里', ''), ('草屯', ''), ('竹山', ''), ('集集', ''), ('名間', ''), ('鹿谷', ''),
        ('中寮', ''), ('魚池', ''), ('國姓', ''), ('水里', ''), ('信義', ''), ('仁愛', ''),
    ],
    ('7129', 4): [
        ('斗六', ''), ('斗南', ''), ('虎尾', ''), ('西螺', ''), ('土庫', ''), ('北港', ''), ('林內', ''),
        ('古坑', ''), ('大埤', ''), ('莿桐', ''), ('褒忠', ''), ('二崙', ''), ('崙背', ''), ('麥寮', ''),
        ('臺西', ''), ('東勢', ''), ('元長', ''), ('四湖', ''), ('口湖', ''), ('水林', ''),
    ],
    ('7130', 4): [
        ('太保', ''), ('朴子', ''), ('布袋', ''), ('大林', ''), ('民雄', ''), ('溪口', ''), ('新港', ''),
        ('六腳', ''), ('東石', ''), ('義竹', ''), ('鹿草', ''), ('水上', ''), ('中埔', ''), ('竹崎', ''),
        ('梅山', ''), ('番路', ''), ('大埔', ''), ('阿里山', ''),
    ],
    ('7133', 4): [
        ('屏東', ''), ('潮州', ''), ('東港', ''), ('恆春', ''), ('萬丹', ''), ('長治', ''), ('麟洛', ''),
        ('九如', ''), ('里港', ''), ('鹽埔', ''), ('高樹', ''), ('萬巒', ''), ('內埔', ''), ('竹田', ''),
        ('新埤', ''), ('枋寮', ''), ('新園', ''), ('崁頂', ''), ('林邊', ''), ('南州', ''), ('佳冬', ''),
        ('琉球', ''), ('車城', ''), ('滿州', ''), ('枋山', ''), ('霧臺', ''), ('瑪家', ''), ('泰武', ''),
        ('來義', ''), ('春日', ''), ('獅子', ''), ('牡丹', ''), ('三地門', ''),
    ],
    ('7134', 4): [
        ('臺東', ''), ('成功', ''), ('關山', ''), ('長濱', ''), ('池上', ''), ('東河', ''), ('鹿野', ''),
        ('卑南', ''), ('大武', ''), ('綠島', ''), ('太麻里', ''), ('海端', ''), ('延平', ''),
        ('金峰', ''), ('達仁', ''), ('蘭嶼', ''),
    ],
    ('7135', 4): [
        ('花蓮', ''), ('鳳林', ''), ('玉里', ''), ('新城', ''), ('吉安', ''), ('壽豐', ''), ('光復', ''),
        ('豐濱', ''), ('瑞穗', ''), ('富里', ''), ('秀林', ''), ('萬榮', ''), ('卓溪', ''),
    ],
    ('7136', 4): [
        ('馬公', ''), ('湖西', ''), ('白沙', ''), ('西嶼', ''), ('望安', ''), ('七美', ''),
    ],
    ('7137', 4): [
        ('金城', ''), ('金湖', ''), ('金沙', ''), ('金寧', ''), ('烈嶼', ''), ('烏坵', ''),
    ],
    ('7138', 4): [
        ('南竿', ''), ('北竿', ''), ('莒光', ''), ('東引', ''),
    ],
    # 香港 18 区、澳门 7 个堂区：只借顺序。香港按 region 分组、组内照这个顺序
    # 从 01 编（见 HK_DISTRICTS），澳门由 new_code 编（820101 起）；站点没有的
    # 新城A区排最后。
    ('81', 3): [
        ('中西', ''), ('東', ''), ('九龍城', ''), ('觀塘', ''), ('南', ''), ('深水埗', ''), ('灣仔', ''),
        ('黃大仙', ''), ('油尖旺', ''), ('離島', ''), ('葵青', ''), ('北', ''), ('西貢', ''),
        ('沙田', ''), ('屯門', ''), ('大埔', ''), ('荃灣', ''), ('元朗', ''),
    ],
    ('82', 3): [
        ('花地瑪', ''), ('聖安多尼', ''), ('望德', ''), ('大堂', ''), ('風順', ''), ('氹仔', ''),
        ('路環', ''),
    ],
}


def ordered(page, units, parent, level):
    """按 HUIRUI_ORDER 排一组同级单位，带出表里的号。

    返回 [(unit, code)]。表里对得上的按站点顺序排前面，code 是表里写的
    （'' 表示只借顺序）；对不上的按页面顺序排在最后，code 为 None。
    有表可查时，两边对不上（表里没有的单位、页面上没有的单位）都打警告。
    """
    order = HUIRUI_ORDER.get((parent, level), [])
    if not order:
        return [(unit, None) for unit in units]
    rank, codes = {}, {}
    for i, (name, code) in enumerate(order):
        rank[name] = i
        codes[name] = code
    seen = set()
    tagged = []
    for i, unit in enumerate(units):
        key = unit['proper']
        if key in rank:
            seen.add(key)
            tagged.append((rank[key], 0, i, unit, codes[key] or None))
        else:
            print(f'警告：{page} {parent}/{level} 的 {unit["full"]} 不在 '
                  f'HUIRUI_ORDER 里，排到最后按本仓库规则补号', file=sys.stderr)
            tagged.append((len(order), 1, i, unit, None))
    for name, _ in order:
        if name not in seen:
            print(f'警告：HUIRUI_ORDER ({parent}, {level}) 里的 {name} '
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
                # 18 区先按 huirui 的顺序排，再按 region 分组，组内从 01 编起：
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


TOPS_CODE = {'taiwan': '71', 'hongkong': '81', 'macau': '82'}


def main():
    used = set()
    fixed = [[level, parent, code, type_out(type_), name]
             for level, parent, code, type_, name in TOPS]
    if not ARGS.no_hk_regions:
        fixed += hk_region_rows(ARGS.hk_regions)
    rows = list(fixed)
    used |= set(row[2] for row in fixed)
    for page in ('taiwan', 'hongkong', 'macau'):
        rows.extend(build(page, used))

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
    ap.add_argument('--hk-regions', nargs='?', const='', default='区域', metavar='TYPE',
                    help='补 香港岛/九龙/新界 三行，18 区挂到 8101/8102/8103 下；'
                         '默认 type=區域（九龍:區域），--hk-regions=TYPE 换 type，'
                         '--hk-regions= （空）则 type 留空、名字不加通名')
    ap.add_argument('--no-hk-regions', action='store_true',
                    help='不补这三行：18 区直接挂 81，按顺序编成 810101-810118')
    ARGS = ap.parse_args()
    main()
