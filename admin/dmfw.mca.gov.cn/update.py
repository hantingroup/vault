"""更新内地的行政区划表：抓 dmfw.mca.gov.cn，拆表并标注，输出到 .output。

    省市区县.csv          1-3 级，带 level
    乡镇街道.csv          4 级，不带 level
    乡镇街道.revoked.csv  已撤销的乡级建制

名称默认用 admin/scripts/ 的 mark.py 标成「专名:通名」，标不出来的保留原名并列到
stderr；--no-mark 关闭标注。--fresh 清掉 .cache/dmfw/dmfw.jsonl 重新抓。
"""

import csv
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from pydantic import BaseModel, model_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# 按脚本位置算路径，跟从哪个目录运行无关
ROOT = Path(__file__).resolve().parent

# 共用的标注脚本在 admin/scripts/：抓下来的名称默认先标成「专名:通名」再落盘
sys.path.insert(0, str(ROOT.parent / 'scripts'))
import mark

# 直辖市代码
MUNICIPALITY_CODES = {'11', '12', '31', '50'}

API_URL = 'https://dmfw.mca.gov.cn/9095/xzqh/getList'
# 接口对没有浏览器头的请求直接返回 403
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
    'Referer': 'https://dmfw.mca.gov.cn/',
}
# 并发再高接口会零星断连，2800 多个区县重跑一遍不划算
MAX_WORKERS = 6
# 抓取结果写到独立目录，不覆盖已人工校订过的 CSV
OUTPUT_DIR = ROOT / '.output'
# 响应缓存是可以重抓的中间产物，单独放，不混进 .output
CACHE_DIR = ROOT / '.cache' / 'dmfw'
# 已撤销的乡级建制，名称形如「北关街道（撤销）」「岭上乡（2021年撤销）」
REVOKED_PATTERN = re.compile(r'（[^）]*撤[销分][^）]*）')
# 同一 code 下发多条记录时的名称表。目前只有场镇合一的别名并列，例如 659001100
# 同时叫 北泉镇 / 石北泉镇 / 石总场北泉镇，接口没有任何字段能区分，
# 按 code 去重会静默丢掉其余写法，所以单独记下来
ALIASES: Dict[str, List[str]] = {}
# 响应缓存，按 (code, maxLevel) 存。接口零星断连时整轮重跑代价太大，
# 每拿到一个响应立刻追加一行落盘，重跑只补缺失的 code
CACHE_PATH = CACHE_DIR / 'dmfw.jsonl'
CACHE: Dict[Tuple[str, int], dict] = {}
CACHE_LOCK = threading.Lock()
# 进度条：宽度以字符计，备注列固定宽度，避免 \\r 刷出残影
BAR_WIDTH = 30
NOTE_WIDTH = 26


class Level(Enum):
    """行政区划层级（不依赖 code 长度存储，仅通过静态方法判断）"""
    NATION = 0
    PROVINCE = 1
    PREFECTURE = 2
    COUNTY = 3
    TOWNSHIP = 4

    @classmethod
    def from_code(cls, code: str) -> 'Level':
        """根据 code 返回对应层级，注意省直辖县级仍属于县级"""
        if code == '00':
            return cls.NATION
        code_len = len(code)
        if code_len == 2:
            return cls.PROVINCE
        elif code_len == 4:
            return cls.PREFECTURE
        elif code_len == 6:
            return cls.COUNTY
        elif code_len == 9:
            return cls.TOWNSHIP
        else:
            raise ValueError(f"Invalid code length: {len(code)}")


class Node(BaseModel):
    code: str
    name: str
    level: int          # 接口返回的层级（保留，但不用于主要逻辑）
    type: str
    children: List['Node'] = []

    @model_validator(mode='before')
    @classmethod
    def preprocess(cls, data):
        if isinstance(data, dict):
            if data.get('code') == '00':
                data['name'] = '全国'
            # 接口不给台湾省的代码，只回一个“资料暂缺”的占位
            if data.get('name') == '台湾省':
                data['code'] = '71'
                data['children'] = []
            if not isinstance(data.get('children'), list):
                data['children'] = []
        return data

    @property
    def parent(self) -> str:
        """根据 code 计算上级 code（直辖市、省直辖县级特殊处理）"""
        code_len = len(self.code)
        if code_len == 2:
            # 省级的上级是「大区」，接口不下发，仓库约定取首位数字：
            # 1 华北 2 东北 3 华东 4 中南 5 西南 6 西北 7 台湾 8 港澳
            return self.code[0]
        elif code_len == 4:
            return self.code[:2]
        elif code_len == 6:
            # 直辖市下属区县，上级为直辖市（前2位）
            if self.code[:2] in MUNICIPALITY_CODES:
                return self.code[:2]
            # 省直辖县级：地级两位是 90（419001 济源、429021 神农架、4690xx 海南、
            # 6590xx 兵团），对应的汇总码 XX9000 两边数据都不收，上级直接取省级。
            # 地级两位是 00 同理，是 dl 给兵团这类不在地级序列里的单位占的位
            if self.code[2:4] in ('90', '00'):
                return self.code[:2]
            # 县级两位 00：整个 code 就是地级级单位（133100 雄安新区），
            # 前四位 1331 是空的
            if self.code[4:6] == '00':
                return self.code[:2]
            return self.code[:4]
        elif code_len == 9:
            # 不设区的地级市（东莞、中山、儋州、嘉峪关）没有县级，子代码里县级
            # 两位是 00（441900003），此时上级取前 4 位指向地级。
            # 和直辖市跳过地级（110101 的上级写 11 而不是 1101）是同一条规则：
            # parent 是行政上级，不是 code 前缀
            if self.code[4:6] == '00':
                return self.code[:4]
            return self.code[:6]
        else:
            raise ValueError(f"Invalid code length: {self.code}")


def load_cache() -> None:
    """读回上次的响应。上次中途被打断时最后一行可能只写了一半，跳过即可"""
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
    """追加一行。多线程并发写，用锁保证一行不被拆开"""
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
    """请求接口，返回原始 data 字段（缓存里存的就是它）"""
    resp = requests.get(API_URL, params={'code': code, 'trimCode': True, 'maxLevel': max_level},
                        headers=HEADERS, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get('data'):
        raise ValueError(f"Unexpected response for {code}: {payload}")
    return payload['data']


def fetch_node(code: str, max_level: int) -> Node:
    """取节点，命中缓存就不再请求。

    maxLevel 是**相对被请求节点的向下层数**，服务端硬限 3，且各层的实际深度是实测的：
        getList('00',     3) -> 省级 + 地级 + 县级（一次 245KB，全拿到）
        getList('1301',   2) -> 石家庄的县级 + 乡级
        getList('110102', 1) -> 西城区的乡级
    从省级再往下要不到乡级（'13' 给 3 也只回到县级），所以乡级只能按地级市或县级请求；
    但一个地级市一次就带回它下面全部县级与乡级，比逐县请求省一个数量级。
    """
    key = (code, max_level)
    data = CACHE.get(key)
    if data is None:
        data = request_node(code, max_level)
        CACHE[key] = data
        save_cache(code, max_level, data)
    return Node.model_validate(data)


def collect(nodes: Dict[str, Node], node: Node) -> None:
    """把节点及其所有后代收进 nodes，顺带记录同一 code 出现过的各个名称"""
    names = ALIASES.setdefault(node.code, [])
    if node.name not in names:
        names.append(node.name)
    nodes[node.code] = node
    for child in node.children:
        collect(nodes, child)


def render_progress(done: int, total: int, note: str = '') -> None:
    """单行进度条，\\r 原地刷新；跑完换行"""
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
    """并发展开一批 code。

    单个 code 失败不中断整轮：先记下失败、跑完再一并报告。成功的响应都已进缓存，
    重跑时只补抓失败的那些，所以宁可这一轮少几条，也不要整轮作废。
    """
    result: Dict[str, Node] = {}
    failures: List[str] = []
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
                failures.append(code)
                errors.append((code, exc))
                render_progress(index, total, f'{code} FAILED')
            else:
                result[code] = node
                render_progress(index, total, f'{code} {node.name}')

    if failures:
        print(f"{len(failures)} node(s) failed:", file=sys.stderr)
        for code, exc in errors:
            print(f"  {code}: {exc}", file=sys.stderr)
    return result, failures


# 省市区县表带 level，与仓库里的 省市区县.csv 一致
COUNTY_FIELDS = ['level', 'parent', 'code', 'type', 'name']
# 乡镇街道表及其撤销表不带 level，与仓库里的对应文件一致
TOWNSHIP_FIELDS = ['parent', 'code', 'type', 'name']


def write_csv(path: Path, fields: List[str], rows: List[tuple]) -> None:
    code_index = fields.index('code')
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(fields)
        for row in sorted(rows, key=lambda r: r[code_index]):
            writer.writerow(row)


def export_csv(nodes: Dict[str, Node], do_mark: bool = True) -> None:
    """从收集到的节点导出 省市区县.csv、乡镇街道.csv 与 乡镇街道.revoked.csv。

    1-3 级进省市区县表，4 级进乡镇街道表（该表不带 level 列，与仓库一致）。
    已撤销的建制都是乡级，不进乡镇街道表（名称已不代表现行区划），
    单独一份等人工处理。

    do_mark（默认开）用 mark.py 把名称标成「专名:通名」；标不出来的保留原名，
    跑完统一列到 stderr，方便人工看。--no-mark 时原样输出。
    """
    OUTPUT_DIR.mkdir(exist_ok=True)

    warnings = mark.Warning_()

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
        # 撤销标记只去掉名称里的括注，parent/code/type 照原样保留
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
    """同一 code 有多个名称的单独记一笔，留人工确认该取哪个。"""
    duplicates = {code: names for code, names in ALIASES.items() if len(names) > 1}
    for code in sorted(duplicates):
        print(f"  {code}: {' | '.join(duplicates[code])}", file=sys.stderr)


def main(argv: List[str]) -> int:
    if '--fresh' in argv:
        CACHE_PATH.unlink(missing_ok=True)
        print(f"Removed cache: {CACHE_PATH}", file=sys.stderr)
    do_mark = '--no-mark' not in argv

    OUTPUT_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    load_cache()

    nodes: Dict[str, Node] = {}
    failures: List[str] = []

    # 第一步：全国一次拿到底（省、地级、县级），maxLevel 再大也不会回乡镇
    print("Step 1: Fetch provinces, prefectures and counties (maxLevel=3)", file=sys.stderr)
    collect(nodes, fetch_node('00', 3))

    # 第二步：按地级市展开，一次带回县级与乡级。直辖市没有地级这一层，按下省码请求；
    # 东莞、中山、儋州、嘉峪关这类不设区的地级市，乡级直接挂在市级，同样被覆盖
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
        # 万一只回了县级、一条乡级都没有，按县级逐个补，交给第三步
        counties = [c for c in node.children if len(c.code) == 6]
        if counties and not any(c.children for c in counties):
            covered.difference_update(c.code for c in counties)

    # 第三步：第二步没覆盖到的县级——省直辖县级市、兵团市等零散单位
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
    sys.exit(main(sys.argv[1:]))
