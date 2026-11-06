# 汉听大字典/行政区划

数据按来源分目录，目录名就是来源域名：

| 目录                   | 来源                                                    | 数据文件                                                     |
| ---------------------- | ------------------------------------------------------- | ------------------------------------------------------------ |
| `dmfw.mca.gov.cn/`   | [中国国家地名信息库](https://dmfw.mca.gov.cn/)           | `xzqh.csv`、`xzqh.revoked.csv` |
| `citypopulation.de/` | [citypopulation.de](https://www.citypopulation.de/)      | `china.csv`、`taiwan.csv`、`hongkong.csv`、`macau.csv` |
| `dsec.gov.mo/`       | [澳门统计暨普查局](https://www.dsec.gov.mo/)《統計年鑑》 | `澳门.csv`                                                 |
| `huiruisoft.com/`    | [huiruisoft](https://www.huiruisoft.com/district/)       | 不单独产出仓库表；台湾、香港的码值和同级顺序取自这里         |

    admin/
      level.py       行政级别 `Level`
      mark.py        「专名:通名」标记与台港澳通名规则
      行政管理区.csv
      <来源域名>/
        *.py         该来源的脚本
        *.json       来源相关的参考数据（JSON 映射和名单）
        *.csv        产出的仓库表
      .output/<来源域名>/  生成的仓库表（核对后覆盖回 <来源域名>/）
      .cache/<来源域名>/   抓取缓存

- `行政管理区.csv`：开发区、管理区这类非标准建制，手工维护，多一列 `raw` 记原始 code。
- `level.py`：行政级别 `Level`（0 国、1 省、2 地、3 县、4 乡），各来源脚本共用。
- `mark.py`：把内地名称标成「专名:通名」，台港澳按 type 定通名；各来源脚本共用。
  内地名称用的数据 `dmfw.mca.gov.cn/names.json` 仍留在来源目录里。
- `dmfw.mca.gov.cn/`：内地数据脚本。`fetch.py` 抓取并生成两张表，
  `merge.py`/`split.py` 合并和拆分表。
- 各来源目录里的 `*.json`：如台湾和香港的码值顺序、澳门分区归组、
  民族名。代码里只放规则和结构常量。

内地数据不包含：

- 加格达奇区等非标准建制，
- 港澳台地区。

台港澳的 type 不跟内地的行政区划分类对齐（台湾没有地级市，省辖市就是「市」，
县下是「縣轄市」），名字和 type 照来源原文写繁体。

## 更新

每个来源一个目录，在目录里跑脚本（路径按脚本位置算，从哪个目录运行都行）：

    cd admin/dmfw.mca.gov.cn && python fetch.py
    cd admin/citypopulation.de && python build.py
    cd admin/dsec.gov.mo && python fetch.py

生成仓库表的脚本写到 `admin/.output/<来源域名>/`，跟已校订的 `<来源域名>/*.csv`
分开；核对后再覆盖过去。抓取缓存放在 `admin/.cache/<来源域名>/`。

- `dmfw.mca.gov.cn/fetch.py`：抓内地省市区县和乡镇街道；`--no-mark`
  不标名称，`--fresh` 清缓存重抓。
- `citypopulation.de/fetch.py`：通用抓取器，给一个 citypopulation.de 的行政区列表页，
  输出页面原始字段（`parent,level,id,status,name,alt,native,wiki,wikidata,area,density`、
  按页头生成的各期人口列 `pop_<C|E>_<date>`、`url`）。全国、国外的页面都能抓，不套仓库格式。
  输出按 `id` 排序（字符串序；`id` 是层级码，子 id 紧随父 id、排在下一个同级之前；无 id 的页首行最前）。
  `-r` 从该页所属的国家/地区首页顺着 `admin/` 列表页往下抓（如中国各省的县区页、
  各市的乡镇页），按 `id` 合并成一张表，`level` 折算到国家页的口径；
  单元详情页只有人口数据，`cities/`、城市群等分区不是行政区划，都不并入；
  统计地区这类比现有层级更粗的页面（如台湾的 Regions & Counties）也不并入。
- `citypopulation.de/build.py`：把台湾、香港页面转成仓库格式；码值和
  同级顺序取自 `../huiruisoft.com/`，`--refresh-order` 重建
  `order.json`。澳门页面的口径跟 DSEC 不同，不在这个脚本里。
- `huiruisoft.com/fetch.py`：通用抓取器，站点覆盖全国（省、市、县、乡镇逐级）和台港澳。
  不写页码时取 `2022` 全国县及以上汇总，也可以指定 `110000` 北京市、`11/110101` 东城区、
  `710000` 台湾省。按页面表头输出 CSV（区划代码、中文名称、电话区号…），不套仓库格式。
  `-r` 顺着页内链接往下抓，各层按代码合并成一张表（全国全层约四万个页面）。
- `dsec.gov.mo/fetch.py`：下载 DSEC《統計年鑑》PDF，解析表 1.1「統計分區」
  生成澳门表。26 个分区的编号和名字取自 PDF（`yearbook.py`），「分区归哪个
  堂區/新城區」写在 `macau.json` 里；年鉴升版后 `--refresh` 重新下载，名字对不上
  会报错。

如有需要可以去看看 [https://github.com/montmorill/data_location](https://github.com/montmorill/data_location)，该仓库包含了更多数据。

## 关于 `name` 的格式

`name` 由专名部分（民族部分使用 `|` 标记）与通名部分用 `:` 连接而成，如：

- `北京:市`、`津市:市`、`辉县:市`，
- (清远市) `清新:区`、`浦东:新区`，
- (阳泉) `矿:区`、`峰峰:矿区`、`神农架:林区`、`六枝:特区`，
- `青龙|满族:自治县`、`威宁|彝族|回族|苗族:自治县`，
- `|东乡族:自治县`、`|鄂伦春:自治旗`、`|鄂温克族:自治旗`（此三县(旗)无地名，直接由“民族名+自治县(旗)”构成），
- `龙胜|各族:自治县`、`隆林|各族:自治县`（“各族”在这里指的是多个民族）。
