#!/usr/bin/env python3
"""把行政区划名称标成「专名:通名」，民族名另用 | 分隔。

    北京市        -> 北京:市              （type=直辖市）
    东城区        -> 东城:区              （type=市辖区）
    浦东新区      -> 浦东:新区
    常营回族乡    -> 常营|回族:乡          （type=民族乡）
    得力其尔鄂温克民族乡 -> 得力其尔|鄂温克:民族乡
    新疆维吾尔自治区 -> 新疆|维吾尔:自治区
    东乡族自治县  -> |东乡族:自治县        （专名本身为空）
    红星二场      -> 红星二:场            （type=农场，名的后缀是「场」）

通名由 type 决定，不是看名称后缀猜的；只有 type 之下还分几种写法时才看后缀
（市辖区 区/新区/矿区、民族乡 乡/民族乡、农场 农场/场）。

用法：
    python mark.py .output/省市区县.csv > marked.csv
    python mark.py 省市区县.csv 乡镇街道.csv > marked.csv    # 列相同的文件才可以一起给

已经标过的名称（带 `|` 或 `:`）会先还原成原名再重标一遍；重标结果与原名不同时
报 warning，输出采用重标的写法。没标过的照常标注，不算 warning。
重复跑不会越标越多。
"""

import csv
import io
import sys
from typing import Dict, List, Optional, Tuple

# 56 个民族，去掉「族」字。匹配时先试「X族」，再试「X」本身——
# 接口里确实有省略「族」的写法（鄂伦春自治旗、新疆维吾尔自治区）
ETHNIC = [
    '蒙古', '回', '藏', '维吾尔', '苗', '彝', '壮', '布依', '朝鲜', '满',
    '侗', '瑶', '白', '土家', '哈尼', '哈萨克', '傣', '黎', '傈僳', '佤',
    '畲', '高山', '拉祜', '水', '东乡', '纳西', '景颇', '柯尔克孜', '土', '达斡尔',
    '仫佬', '羌', '布朗', '撒拉', '毛南', '仡佬', '锡伯', '阿昌', '普米', '塔吉克',
    '怒', '乌孜别克', '俄罗斯', '鄂温克', '德昂', '保安', '裕固', '京', '塔塔尔', '独龙',
    '鄂伦春', '赫哲', '门巴', '珞巴', '基诺',
]
# 长的先匹配，否则「土」会抢走「土家」
ETHNIC.sort(key=len, reverse=True)

# type -> 通名。None 表示该 type 下还有几种写法，交给 _generics() 按后缀区分
GENERIC: Dict[str, Optional[str]] = {
    '省': '省', '自治区': '自治区', '直辖市': '市', '特别行政区': '特别行政区',
    '地级市': '市', '县级市': '市', '地区': '地区', '盟': '盟', '自治州': '自治州',
    '市辖区': None, '县': '县', '自治县': '自治县', '旗': '旗', '自治旗': '自治旗',
    '林区': '林区', '特区': '特区',
    '镇': '镇', '乡': '乡', '街道': '街道', '苏木': '苏木',
    '民族乡': None, '民族苏木': '民族苏木', '区公所': '区公所',
    '团': '团', '农场': None, '牧场': '牧场',
}
# 通名里还要把尾部民族名拆出来的 type
ETHNIC_TYPES = {'自治区', '自治州', '自治县', '自治旗', '民族乡', '民族苏木'}
# 整名带「新区」「矿区」但通名其实是「区」的少数几个，光看名称分不出来
GENERIC_OVERRIDES = {'441803': '区'}          # 清远市清新区

# 名称省略「族」时，拆完专名至少留两个字——否则「内蒙古自治区」会被拆成「内|蒙古」。
# 这条只挡「内」这种一字残名，拆成空专名是允许的（东乡族自治县 -> |东乡族:自治县）
MIN_KEPT_STEM = 2


class Warning_(object):
    def __init__(self):
        self.rows: List[Tuple[str, str, str]] = []          # code, type, why：标不出来
        self.changed: List[Tuple[str, str, str, str]] = []  # code, type, 原名, 重标结果

    def add(self, code: str, typ: str, why: str) -> None:
        self.rows.append((code, typ, why))

    def change(self, code: str, typ: str, old: str, new: str) -> None:
        self.changed.append((code, typ, old, new))

    def dump(self) -> None:
        if self.changed:
            print(f"{len(self.changed)} 行重标后与原名不同（输出用重标写法）：", file=sys.stderr)
            for code, typ, old, new in self.changed:
                print(f"  {code} [{typ}] {old} -> {new}", file=sys.stderr)
        if self.rows:
            print(f"{len(self.rows)} 行没能标出通名：", file=sys.stderr)
            for code, typ, why in self.rows:
                print(f"  {code} [{typ}] {why}", file=sys.stderr)


WARN = Warning_()


def split_ethnic(stem: str, allow_bare: bool, min_kept: int = MIN_KEPT_STEM) -> Tuple[str, List[str]]:
    """把尾部连续的民族名拆出来，返回 (专名, [民族名...])。

    「族」是名称里真实存在的字，拆下来的片段原样保留，所以
    新疆维吾尔自治区 -> ('新疆', ['维吾尔'])，宁夏回族自治区 -> ('宁夏', ['回族'])

    名称里带「族」的写法没有歧义，任何 type 都拆（二十家子满族镇、管城回族区）。
    省略「族」的写法（鄂伦春自治旗、新疆维吾尔自治区）只在自治州县旗区下才有，
    而且要防「内蒙古自治区」被拆成「内|蒙古」，所以拆完要求专名不是孤零零一个字。
    """
    parts: List[str] = []
    while stem:
        if stem.endswith('各族'):        # 「各族自治县」里的各族不是民族名
            parts.insert(0, '各族')
            stem = stem[:-2]
            continue
        for name in ETHNIC:
            suffix = name + '族'
            if stem.endswith(suffix):
                parts.insert(0, suffix)
                stem = stem[:-len(suffix)]
                break
        else:
            break

    if parts or not allow_bare:
        return stem, parts

    while stem:
        for name in ETHNIC:
            if not stem.endswith(name):
                continue
            kept = stem[:-len(name)]
            if kept and len(kept) < min_kept:
                continue
            parts.insert(0, name)
            stem = kept
            break
        else:
            break
    return stem, parts


def _generics(typ: str, name: str, code: str = '') -> List[str]:
    """候选通名，长的在前。

    type 之下不止一种写法时按名称后缀给候选，例如市辖区 新区/矿区/区。
    长的先试，试不通再退到短的——阳泉市的「矿区」整名就是通名，
    退到「区」才拆得成「矿:区」，与本仓库已有写法一致。
    """
    if typ == '市辖区':
        if code in GENERIC_OVERRIDES:
            return [GENERIC_OVERRIDES[code]]
        return [c for c in ('新区', '矿区', '区') if name.endswith(c)]
    if typ == '民族乡':
        return [c for c in ('民族乡', '乡') if name.endswith(c)]
    if typ == '农场':
        return [c for c in ('农场', '场') if name.endswith(c)]
    generic = GENERIC.get(typ)
    return [generic] if generic else []


def _mark_with(name: str, typ: str, generic: str) -> Optional[str]:
    if not name.endswith(generic):
        return None
    stem = name[:-len(generic)]
    race = ''
    if typ in ETHNIC_TYPES:
        # 民族乡本身就是「民族」写进通名的，专名只剩一个字也照拆（勒|门巴:民族乡）
        min_kept = 0 if typ in ('民族乡', '民族苏木') else MIN_KEPT_STEM
        stem, parts = split_ethnic(stem, allow_bare=True, min_kept=min_kept)
        race = ''.join(f'|{p}' for p in parts)
    else:
        # 镇、街道、市辖区里也常有民族名（二十家子满族镇、管城回族区），
        # 但这只认写了「族」的写法，不然「保安镇」「东乡镇」这类地名会被拆错
        stem, parts = split_ethnic(stem, allow_bare=False)
        race = ''.join(f'|{p}' for p in parts)
    if not stem and not race:
        return None
    return f'{stem}{race}:{generic}'


def mark(name: str, typ: str, code: str = '') -> Optional[str]:
    """返回标注后的名称；标不出来返回 None。

    `|` 与 `:` 是本脚本的输出记号，不属于原名：一律先去掉再重标。这样旧标注
    写错的地方会被纠正，重复跑也仍然稳定。
    """
    name = name.replace('|', '').replace(':', '')
    for generic in _generics(typ, name, code):
        result = _mark_with(name, typ, generic)
        if result is not None:
            return result
    return None


def main(argv: List[str]) -> int:
    paths = argv or ['.output/省市区县.csv']
    out = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', newline='\n')
    writer = csv.writer(out, lineterminator='\n')
    header_written = False
    total = marked = 0

    for path in paths:
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if not header_written:
                writer.writerow(reader.fieldnames)
                header_written = True
            for row in reader:
                total += 1
                code = row.get('code', '?')
                old = row['name']
                new = mark(old, row['type'], row.get('code', ''))
                if new is None:
                    WARN.add(code, row['type'], old)
                else:
                    # 本来没标注的只是正常标注；已带 `|`/`:` 的旧标注才算对不上
                    if new != old and ('|' in old or ':' in old):
                        WARN.change(code, row['type'], old, new)
                    row['name'] = new
                    marked += 1
                writer.writerow([row[k] for k in reader.fieldnames])

    out.flush()
    WARN.dump()
    print(f"{marked}/{total} 行已标注", file=sys.stderr)
    return 0 if marked == total else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
