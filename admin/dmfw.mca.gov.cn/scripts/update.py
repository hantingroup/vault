"""Update the mainland administrative division tables from dmfw.mca.gov.cn.

Fetches the hierarchy, splits it by level and marks names, writing to .output:

    省市区县.csv          levels 1-3, with a level column
    乡镇街道.csv          level 4, without a level column
    乡镇街道.revoked.csv  revoked township-level divisions

Names are marked as 「专名:通名」 by scripts/mark.py; names that cannot be marked
are kept as-is and listed on stderr. --no-mark disables marking. --fresh deletes
.cache/dmfw.jsonl and refetches everything.
"""

import csv
import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from pydantic import BaseModel, model_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent.parent))
import mark
from level import Level

# Municipality codes
MUNICIPALITY_CODES = {'11', '12', '31', '50'}

API_URL = 'https://dmfw.mca.gov.cn/9095/xzqh/getList'
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
    'Referer': 'https://dmfw.mca.gov.cn/',
}
MAX_WORKERS = 6
OUTPUT_DIR = ROOT / '.output'
CACHE_DIR = ROOT / '.cache'
# Revoked township-level divisions, e.g. 「北关街道（撤销）」 or 「岭上乡（2021年撤销）」
REVOKED_PATTERN = re.compile(r'（[^）]*撤[销分][^）]*）')
# Every name seen for a code. Some codes merge a town and a farm and come back
# with several names (659001100: 北泉镇 / 石北泉镇 / 石总场北泉镇); the API has no
# field that tells them apart.
ALIASES: Dict[str, List[str]] = {}
# Response cache keyed by (code, maxLevel), one JSON object per line
CACHE_PATH = CACHE_DIR / 'dmfw.jsonl'
CACHE: Dict[Tuple[str, int], dict] = {}
CACHE_LOCK = threading.Lock()
BAR_WIDTH = 30
NOTE_WIDTH = 26


class Node(BaseModel):
    code: str
    name: str
    level: int          # from the API; unused by the main logic
    type: str
    children: List['Node'] = []

    @model_validator(mode='before')
    @classmethod
    def preprocess(cls, data):
        if isinstance(data, dict):
            if data.get('code') == '00':
                data['name'] = '全国'
            # Taiwan province comes back as a placeholder without a code
            if data.get('name') == '台湾省':
                data['code'] = '71'
                data['children'] = []
            if not isinstance(data.get('children'), list):
                data['children'] = []
        return data

    @property
    def parent(self) -> str:
        """Parent code derived from the code itself; municipalities and province-administered units are the special cases."""
        code_len = len(self.code)
        if code_len == 2:
            # Provinces report to a region, which the API does not provide; the
            # repository uses the first digit: 1 North, 2 Northeast, 3 East,
            # 4 Central-South, 5 Southwest, 6 Northwest, 7 Taiwan, 8 Hong Kong/Macau.
            return self.code[0]
        elif code_len == 4:
            return self.code[:2]
        elif code_len == 6:
            if self.code[:2] in MUNICIPALITY_CODES:
                return self.code[:2]
            # 90/00 in the prefecture digits mark a province-administered unit
            # (419001 济源, 429021 神农架, 4690xx 海南, 6590xx 兵团); the parent is
            # the province.
            if self.code[2:4] in ('90', '00'):
                return self.code[:2]
            # County digits 00: the code is itself a prefecture-level unit (133100 雄安新区)
            if self.code[4:6] == '00':
                return self.code[:2]
            return self.code[:4]
        elif code_len == 9:
            # Prefecture-level cities without districts (东莞, 中山, 儋州, 嘉峪关)
            # hang townships under the city node, with county digits 00 (441900003).
            if self.code[4:6] == '00':
                return self.code[:4]
            return self.code[:6]
        else:
            raise ValueError(f"Invalid code length: {self.code}")


def load_cache() -> None:
    """Load cached responses, skipping malformed lines."""
    if not CACHE_PATH.exists():
        return
    skipped = 0
    with open(CACHE_PATH, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                CACHE[(entry['code'], entry['max_level'])] = entry['data']
            except (ValueError, KeyError):
                skipped += 1
    message = f"Resume: {len(CACHE)} cached response(s)"
    if skipped:
        message += f", {skipped} malformed line(s) skipped"
    print(message, file=sys.stderr)


def save_cache(code: str, max_level: int, data: dict) -> None:
    with CACHE_LOCK:
        with open(CACHE_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'code': code, 'max_level': max_level, 'data': data},
                               ensure_ascii=False) + '\n')


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, ValueError)),
    reraise=True
)
def request_node(code: str, max_level: int) -> dict:
    resp = requests.get(API_URL, params={'code': code, 'trimCode': True, 'maxLevel': max_level},
                        headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get('data'):
        raise ValueError(f"Unexpected response for {code}: {payload}")
    return payload['data']


def fetch_node(code: str, max_level: int) -> Node:
    """Return a node, served from cache when available.

    maxLevel is the number of levels below the requested node, capped by the
    server at 3:
        getList('00',     3) -> provinces, prefectures and counties (245 KB, all of them)
        getList('1301',   2) -> 石家庄's counties and townships
        getList('110102', 1) -> 西城区's townships
    Requests above prefecture level stop at counties, so townships are fetched
    per prefecture or county; a single prefecture returns all of its counties
    and townships at once.
    """
    key = (code, max_level)
    data = CACHE.get(key)
    if data is None:
        data = request_node(code, max_level)
        CACHE[key] = data
        save_cache(code, max_level, data)
    return Node.model_validate(data)


def collect(nodes: Dict[str, Node], node: Node) -> None:
    """Add a node and its descendants to nodes, recording every name seen."""
    names = ALIASES.setdefault(node.code, [])
    if node.name not in names:
        names.append(node.name)
    nodes[node.code] = node
    for child in node.children:
        collect(nodes, child)


def render_progress(done: int, total: int, note: str = '') -> None:
    if total:
        filled = BAR_WIDTH * done // total
        percent = done * 100 // total
    else:
        filled, percent = BAR_WIDTH, 100
    bar = '█' * filled + '░' * (BAR_WIDTH - filled)
    print(f'\r{bar} {done}/{total} ({percent:3d}%) {note[:NOTE_WIDTH]:<{NOTE_WIDTH}}',
          end='', file=sys.stderr, flush=True)
    if done == total:
        print(file=sys.stderr)


def expand(codes: List[str], max_level: int) -> Tuple[Dict[str, Node], List[str]]:
    """Expand codes concurrently; failing codes are returned rather than raised."""
    result: Dict[str, Node] = {}
    errors: List[Tuple[str, Exception]] = []

    def work(code: str):
        try:
            return code, fetch_node(code, max_level), None
        except Exception as exc:
            return code, None, exc

    total = len(codes)
    with ThreadPoolExecutor(MAX_WORKERS) as pool:
        for index, (code, node, exc) in enumerate(pool.map(work, codes), start=1):
            if exc is not None:
                errors.append((code, exc))
                render_progress(index, total, f'{code} FAILED')
            else:
                result[code] = node
                render_progress(index, total, f'{code} {node.name}')

    if errors:
        print(f"{len(errors)} node(s) failed:", file=sys.stderr)
        for code, exc in errors:
            print(f"  {code}: {exc}", file=sys.stderr)
    return result, [code for code, _ in errors]


# The county table has a level column; the township and revoked tables do not
COUNTY_FIELDS = ['level', 'parent', 'code', 'type', 'name']
TOWNSHIP_FIELDS = ['parent', 'code', 'type', 'name']


def write_csv(path: Path, fields: List[str], rows: List[tuple]) -> None:
    code_index = fields.index('code')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(fields)
        for row in sorted(rows, key=lambda r: r[code_index]):
            writer.writerow(row)


def export_csv(nodes: Dict[str, Node], do_mark: bool = True) -> None:
    """Write the three CSVs from the collected nodes.

    Levels 1-3 go to the county table, level 4 to the township table. Revoked
    divisions are written separately with their parenthetical marker removed.
    With do_mark (the default) names are marked by mark.py; names that cannot be
    marked are kept as-is and listed on stderr.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)

    warnings = mark.Warnings()

    def name_of(node: Node, name: str) -> str:
        if not do_mark:
            return name
        marked = mark.mark(name, node.type, node.code)
        if marked is None:
            warnings.add(node.code, node.type, name)
            return name
        return marked

    county_rows = []
    township_rows = []
    revoked_rows = []
    for node in sorted(nodes.values(), key=lambda n: n.code):
        try:
            level = Level.from_code(node.code)
        except ValueError:
            continue
        if level is Level.NATION:
            continue
        if REVOKED_PATTERN.search(node.name):
            name = name_of(node, REVOKED_PATTERN.sub('', node.name))
            revoked_rows.append((node.parent, node.code, node.type, name))
        elif level is Level.TOWNSHIP:
            township_rows.append([node.parent, node.code, node.type,
                                  name_of(node, node.name)])
        else:
            county_rows.append([level.value, node.parent, node.code, node.type,
                                name_of(node, node.name)])

    warnings.dump()
    write_csv(OUTPUT_DIR / '省市区县.csv', COUNTY_FIELDS, county_rows)
    print(f"Exported 省市区县.csv with {len(county_rows)} records", file=sys.stderr)
    write_csv(OUTPUT_DIR / '乡镇街道.csv', TOWNSHIP_FIELDS, township_rows)
    print(f"Exported 乡镇街道.csv with {len(township_rows)} records", file=sys.stderr)
    write_csv(OUTPUT_DIR / '乡镇街道.revoked.csv', TOWNSHIP_FIELDS, revoked_rows)
    print(f"Exported 乡镇街道.revoked.csv with {len(revoked_rows)} records", file=sys.stderr)


def report_aliases() -> None:
    duplicates = {code: names for code, names in ALIASES.items() if len(names) > 1}
    for code in sorted(duplicates):
        print(f"  {code}: {' | '.join(duplicates[code])}", file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Update the mainland administrative division tables from dmfw.mca.gov.cn.',
        epilog='Writes 省市区县.csv, 乡镇街道.csv and 乡镇街道.revoked.csv to .output/.')
    parser.add_argument('--fresh', action='store_true',
                        help='delete the response cache and refetch every node')
    parser.add_argument('--no-mark', action='store_true',
                        help='write names as fetched, without 「专名:通名」 marking')
    args = parser.parse_args(argv)

    if args.fresh:
        CACHE_PATH.unlink(missing_ok=True)
        print(f"Removed cache: {CACHE_PATH}", file=sys.stderr)
    do_mark = not args.no_mark

    OUTPUT_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    load_cache()

    nodes: Dict[str, Node] = {}
    failures: List[str] = []

    print("Step 1: Fetch provinces, prefectures and counties (maxLevel=3)", file=sys.stderr)
    collect(nodes, fetch_node('00', 3))

    # Municipalities have no prefecture level and are requested by province code
    expand_codes = sorted(
        code for code in nodes
        if len(code) == 4 or (len(code) == 2 and code in MUNICIPALITY_CODES)
    )
    print(f"Step 2: Expand {len(expand_codes)} prefecture-level nodes (maxLevel=2)", file=sys.stderr)
    expanded, failed = expand(expand_codes, 2)
    failures += failed
    covered = set()
    for node in expanded.values():
        collect(nodes, node)
        covered.update(child.code for child in node.children)
        # Counties with no townships below them are retried individually in step 3
        counties = [c for c in node.children if len(c.code) == 6]
        if counties and not any(c.children for c in counties):
            covered.difference_update(c.code for c in counties)

    county_codes = sorted(code for code in nodes if len(code) == 6 and code not in covered)
    print(f"Step 3: Expand {len(county_codes)} remaining county-level nodes (maxLevel=1)", file=sys.stderr)
    expanded, failed = expand(county_codes, 1)
    failures += failed
    for node in expanded.values():
        collect(nodes, node)

    export_csv(nodes, do_mark)
    report_aliases()

    if failures:
        print(f"{len(failures)} node(s) missing; rerun to retry only these "
              f"(cache: {CACHE_PATH}).", file=sys.stderr)
        return 1
    print("All done.", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
