#!/usr/bin/env python3
"""Mark administrative division names as 「专名:通名」, with ethnic names separated by |.

    北京市        -> 北京:市              (type=直辖市)
    东城区        -> 东城:区              (type=市辖区)
    浦东新区      -> 浦东:新区
    常营回族乡    -> 常营|回族:乡          (type=民族乡)
    得力其尔鄂温克民族乡 -> 得力其尔|鄂温克:民族乡
    新疆维吾尔自治区 -> 新疆|维吾尔:自治区
    东乡族自治县  -> |东乡族:自治县        (empty proper name)
    红星二场      -> 红星二:场            (type=农场, name ends in 场)

The generic part comes from type rather than from the name suffix; only types
with several possible forms are disambiguated by suffix (市辖区: 区/新区/矿区,
民族乡: 乡/民族乡, 农场: 农场/场).

The rules above are for mainland names (dmfw.mca.gov.cn). Hong Kong, Macau and
Taiwan do not follow the mainland classification (Taiwan has no prefecture
level, a province-administered city is a 市, and below a county comes a 縣轄市),
so their types and names stay in traditional Chinese; suffix_of, split_name and
proper_of below handle them for citypopulation.de and dsec.gov.mo.

All source directories under admin/ share this module.

Usage (from the repository root):
    python admin/mark.py admin/.output/dmfw.mca.gov.cn/xzqh.csv > marked.csv

Files passed together must have the same columns: the first file's header is
written once for all rows.

Names that are already marked (they contain | or :) are restored to the plain
name and marked again. When the new result differs, a warning is reported and
the new form is written. Repeated runs are stable.
"""

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The names are mainland-specific, so the data stays with the dmfw source
DATA_PATH = (Path(__file__).resolve().parent
             / 'dmfw.mca.gov.cn' / 'names.json')
DATA = json.loads(DATA_PATH.read_text(encoding='utf-8'))
OUTPUT_DIR = Path(__file__).resolve().parent / '.output' / 'dmfw.mca.gov.cn'

# The 55 ethnic names other than 汉, without 族; matching tries "X族" first,
# then the bare name.
# Longest first, so 土 does not match before 土家.
ETHNIC = sorted(DATA['ethnic'], key=len, reverse=True)

# type -> generic part; None means _generics() picks one from the name suffix
GENERIC: Dict[str, Optional[str]] = {
    '省': '省', '自治区': '自治区', '直辖市': '市', '特别行政区': '特别行政区',
    '地级市': '市', '县级市': '市', '地区': '地区', '盟': '盟', '自治州': '自治州',
    '市辖区': None, '县': '县', '自治县': '自治县', '旗': '旗', '自治旗': '自治旗',
    '林区': '林区', '特区': '特区',
    '镇': '镇', '乡': '乡', '街道': '街道', '苏木': '苏木',
    '民族乡': None, '民族苏木': '民族苏木', '区公所': '区公所',
    '团': '团', '农场': None, '牧场': '牧场',
}
# Types whose generic part ends with an ethnic name that is split off separately
ETHNIC_TYPES = {'自治区', '自治州', '自治县', '自治旗', '民族乡', '民族苏木'}
# Names containing 新区 or 矿区 whose generic part is nevertheless 区, keyed by code
GENERIC_OVERRIDES = DATA['generic_overrides']   # 441803: 清远市清新区

# Minimum stem length when a name omits 族, so 内蒙古自治区 does not split as 内|蒙古
MIN_KEPT_STEM = 2


class Warnings(object):
    def __init__(self):
        self.rows: List[Tuple[str, str, str]] = []          # code, type, name: could not be marked
        self.changed: List[Tuple[str, str, str, str]] = []  # code, type, old name, new name

    def add(self, code: str, typ: str, why: str) -> None:
        self.rows.append((code, typ, why))

    def change(self, code: str, typ: str, old: str, new: str) -> None:
        self.changed.append((code, typ, old, new))

    def dump(self) -> None:
        if self.changed:
            print(f"{len(self.changed)} row(s) re-marked differently (the new form is written):",
                  file=sys.stderr)
            for code, typ, old, new in self.changed:
                print(f"  {code} [{typ}] {old} -> {new}", file=sys.stderr)
        if self.rows:
            print(f"{len(self.rows)} row(s) without a generic part:", file=sys.stderr)
            for code, typ, why in self.rows:
                print(f"  {code} [{typ}] {why}", file=sys.stderr)


WARN = Warnings()


def split_ethnic(stem: str, allow_bare: bool, min_kept: int = MIN_KEPT_STEM) -> Tuple[str, List[str]]:
    """Split trailing ethnic names off the stem, returning (proper name, [ethnic names]).

    Names containing 族 are split for any type (二十家子满族镇, 管城回族区). A name
    that omits 族 (鄂伦春自治旗) is split only when allow_bare is set, keeping at
    least min_kept characters in the stem.
    """
    parts: List[str] = []
    while stem:
        if stem.endswith('各族'):        # 各族 in 各族自治县 is not an ethnic name
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
    """Candidate generic parts, longest first."""
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
    if typ in ETHNIC_TYPES:
        # 民族乡 contains 民族 in its generic, so a one-character stem still splits
        min_kept = 0 if typ in ('民族乡', '民族苏木') else MIN_KEPT_STEM
        stem, parts = split_ethnic(stem, allow_bare=True, min_kept=min_kept)
    else:
        # Towns, streets and districts can contain ethnic names too, but only
        # the 族 spelling is split (二十家子满族镇, 管城回族区)
        stem, parts = split_ethnic(stem, allow_bare=False)
    race = ''.join(f'|{p}' for p in parts)
    if not stem and not race:
        return None
    return f'{stem}{race}:{generic}'


def mark(name: str, typ: str, code: str = '') -> Optional[str]:
    """Return the marked name, or None if it cannot be marked.

    | and : are output markers, not part of the name, so both are stripped
    before marking; repeated runs stay stable.
    """
    name = name.replace('|', '').replace(':', '')
    for generic in _generics(typ, name, code):
        result = _mark_with(name, typ, generic)
        if result is not None:
            return result
    return None


# --- Hong Kong, Macau and Taiwan -----------------------------------------
# These regions do not follow the mainland classification, so page names and
# types stay in traditional Chinese. Used by citypopulation.de/build.py and
# dsec.gov.mo/fetch.py.

# Types whose generic part is not the type name itself; None means the name has
# no generic part (Hong Kong's 選區 are like 寶翠, 衛城). Every other type uses
# the type name as the generic.
SUFFIX_EXCEPTIONS = {
    '直轄市': '市', '市轄區': '區', '縣轄市': '市', '區議會': '區', '選區': None,
    '統計區': '區',
}

# Per-name generic parts where the printed name itself differs from the type's
# generic: 大堂區 is the one parish whose name ends in 區 instead of 堂區.
NAME_SUFFIX_EXCEPTIONS = {('堂區', '大堂'): '區'}


def suffix_of(type_):
    """Generic part for a type: the type itself unless SUFFIX_EXCEPTIONS says otherwise."""
    return SUFFIX_EXCEPTIONS.get(type_, type_)


def split_name(name, type_):
    """Split a page name into the repository's 「专名:通名」.

    Two names lack or shorten the generic part, so it is restored first:
        堂區    the page writes 堂區 as 堂 (花地瑪堂, 聖安多尼堂…); the trailing
                堂 is dropped before 堂區 is appended. 大堂 keeps its 堂, since
                dropping it would leave only 大; its printed name 大堂區 takes
                區 as the generic (see NAME_SUFFIX_EXCEPTIONS).
        區議會  the 區 is missing from 深水埗 (the other 17 have it) and is appended.
    """
    suffix = NAME_SUFFIX_EXCEPTIONS.get((type_, name), suffix_of(type_))
    if suffix is None:
        return name
    if type_ == '堂區' and name.endswith('堂') and len(name) >= 3:
        name = name[:-1]
    if name.endswith(suffix) and len(name) > len(suffix):
        return f'{name[:-len(suffix)]}:{suffix}'
    return f'{name}:{suffix}'


def proper_of(name):
    """Return the proper-name part of 「专名:通名」."""
    return name.split(':')[0]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Mark administrative division names as 「专名:通名」 and write CSV to stdout.',
        epilog='Names already containing | or : are re-marked from the plain name; '
               'results that differ are reported on stderr.')
    parser.add_argument('paths', nargs='*', metavar='CSV',
                        default=[str(OUTPUT_DIR / 'xzqh.csv')],
                        help='CSV file(s) with name/type/code columns '
                             '(default: admin/.output/dmfw.mca.gov.cn/xzqh.csv)')
    args = parser.parse_args(argv)
    paths = args.paths
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
                    # Only an existing |/: form that changes counts as a mismatch
                    if new != old and ('|' in old or ':' in old):
                        WARN.change(code, row['type'], old, new)
                    row['name'] = new
                    marked += 1
                writer.writerow([row[k] for k in reader.fieldnames])

    out.flush()
    WARN.dump()
    print(f"{marked}/{total} rows marked", file=sys.stderr)
    return 0 if marked == total else 1


if __name__ == '__main__':
    sys.exit(main())
