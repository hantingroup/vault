"""更新《明日方舟》干员表：从 prts.wiki 导出 Cargo 表，反转义后写到 ../characters.csv。

prts.wiki 前面挂着 Anubis 的 PoW 挑战，requests/curl 只会拿到挑战页，所以这里用
Playwright 起一个真浏览器把挑战跑过去（通常 1~3 秒），再截下浏览器的下载。

已经自己下载好原始 CSV（例如用 fetch-characters.http 导出后另存）时，用 --from
跳过抓取，只做反转义：

    python update.py                    # 抓取 + 反转义
    python update.py --from results.csv # 只反转义
"""

import argparse
import html
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

# 导出脚本落在本目录，CSV 落在上一级；按脚本位置算，跟从哪个目录运行无关
CSV_PATH = Path(__file__).resolve().parent.parent / 'characters.csv'
# 服务端给下载文件起的名字，只用来存临时文件
RAW_NAME = 'results.csv'
# Cargo 导出参数。fields 里「字段=列名」，列名按顺序原样成为表头
EXPORT_PARAMS = {
    'title': 'Special:CargoExport',
    'tables': 'chara,chara_data,chara_extra_info,char_obtain',
    'join_on': ('chara._pageName=chara_data._pageName,'
                'chara._pageName=chara_extra_info._pageName,'
                'chara._pageName=char_obtain._pageName'),
    'fields': ('chara._pageName=干员,chara.profession=职业,chara.rarity=稀有度,'
               'chara.logo=标志,chara_extra_info.birthPlace=出身地,'
               'chara.team=团队,chara_extra_info.race=种族,chara.en=干员西文名,'
               'chara.jp=干员日文名,chara.eid=情报编号,chara_extra_info.sex=性别,'
               'chara.position=位置,chara.tag=标签,chara.subProfession=子职业,'
               'chara.nation=国家,chara.org=组织'),
    'where': 'chara.charIndex>0',
    'order_by': 'chara.charIndex ASC',
    'limit': '5000',
    'offset': '0',
    'format': 'csv',
}
EXPORT_URL = 'https://prts.wiki/index.php?' + urllib.parse.urlencode(EXPORT_PARAMS)
HOME_URL = 'https://prts.wiki/'
# Anubis 见到 HeadlessChrome 的 UA 直接回 Access Denied，换成普通 Chrome 才会下发挑战
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36')
# 挑战页跑在 sisyphus.prts.wiki 上，过完会跳回 prts.wiki，URL 里能看到主机名
CHALLENGE_HOST = 'sisyphus.'
# 等待挑战完成的轮数和步长（秒）：正常 1~3 秒，给足 30 秒余量
POLL_TIMES = 60
POLL_INTERVAL = 0.5
# 反转义后不该再残留实体；留几个当异常提示
ENTITY_PATTERN = re.compile(r'&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);')


def wait_for_challenge(page: Page) -> None:
    """等 Anubis 的 PoW 自己算完并跳回站点"""
    for _ in range(POLL_TIMES):
        try:
            title = page.title()
        except PlaywrightError:
            title = ''  # 跳转途中读不到标题，下一轮再看
        if CHALLENGE_HOST not in page.url and 'bot' not in title.lower():
            return
        page.wait_for_timeout(POLL_INTERVAL * 1000)
    raise RuntimeError('Anubis 挑战没能在 30 秒内通过')


def fetch_raw() -> bytes:
    """打开浏览器过掉挑战，把导出 URL 的下载内容读成 bytes"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale='zh-CN', user_agent=USER_AGENT,
                                      accept_downloads=True)
        page = context.new_page()
        try:
            page.goto(HOME_URL, wait_until='domcontentloaded', timeout=60_000)
            wait_for_challenge(page)

            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / RAW_NAME
                with page.expect_download(timeout=60_000) as download_info:
                    try:
                        page.goto(EXPORT_URL, timeout=60_000)
                    except PlaywrightError:
                        # 导出地址直接返回附件，浏览器把这次导航中断掉，下载照常触发
                        pass
                download_info.value.save_as(target)
                return target.read_bytes()
        finally:
            browser.close()


def unescape(text: str) -> str:
    """反转义整份文本。

    导出会把单元格按 HTML 实体转义，直接解码的话，被引号包着的单元格里出现
    &quot; 就变成未转义的引号，CSV 解析会错位。所以先把 &quot; 换成 CSV 的 ""，
    再做实体解码——和原来的 unescape.sh 同一套做法，连字段的加引号风格也原样保留。
    """
    return html.unescape(text.replace('&quot;', '""'))


def check(text: str) -> None:
    """表头对不上通常说明抓到的是挑战页或权限页，别写进 CSV"""
    columns = [field.split('=', 1)[-1] for field in EXPORT_PARAMS['fields'].split(',')]
    header = text.split('\n', 1)[0]
    if header != ','.join(columns):
        sys.exit(f'表头不符，可能没抓到 CSV：{header[:80]!r}')
    leftovers = ENTITY_PATTERN.findall(text)
    if leftovers:
        print(f'警告：还有 {len(leftovers)} 处实体没解掉，例如 {leftovers[0]}', file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description='更新《明日方舟》干员表')
    parser.add_argument('--from', dest='source', metavar='CSV', type=Path,
                        help='改成读本地已下载的原始 CSV，不联网抓取')
    args = parser.parse_args()

    if args.source:
        print(f'读取 {args.source}')
        raw = args.source.read_bytes()
    else:
        print('抓取 prts.wiki（需要过 Anubis 挑战，几秒钟）')
        raw = fetch_raw()

    text = unescape(raw.decode('utf-8-sig'))
    check(text)
    CSV_PATH.write_bytes(text.encode('utf-8'))
    print(f'写入 {CSV_PATH}（{len(text.splitlines()) - 1} 行）')


if __name__ == '__main__':
    main()
