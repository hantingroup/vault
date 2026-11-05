# 汉听大字典/行政区划

数据按来源分目录，目录名就是来源域名：

| 目录 | 来源 | 数据文件 |
| --- | --- | --- |
| `dmfw.mca.gov.cn/` | [中国国家地名信息库](https://dmfw.mca.gov.cn/) | `省市区县.csv`、`乡镇街道.csv`、`乡镇街道.revoked.csv` |
| `citypopulation.de/` | [citypopulation.de](https://www.citypopulation.de/) | `台湾.csv`、`香港.csv` |
| `dsec.gov.mo/` | [澳门统计暨普查局](https://www.dsec.gov.mo/)《統計年鑑》 | `澳门.csv` |
| `huiruisoft.com/` | [huiruisoft](https://www.huiruisoft.com/district/) | 不单独产出仓库表；台湾、香港的码值和同级顺序取自这里 |

- `行政管理区.csv`：开发区、管理区这类非标准建制，手工维护，多一列 `raw` 记原始 code。
- `scripts/`：各来源共用的脚本。`mark.py` 把名称标成「专名:通名」，`merge.py`/`split.py`
  合并和拆分表，`gat_common.py` 是台港澳共用的 type 与通名规则。

内地数据不包含：

- 加格达奇区等非标准建制，
- 港澳台地区。

台港澳的 type 不跟内地的行政区划分类对齐（台湾没有地级市，省辖市就是「市」，
县下是「縣轄市」），名字和 type 照来源原文写繁体。

## 更新

每个来源一个目录，在目录里跑脚本（路径按脚本位置算，从哪个目录运行都行）：

    cd admin/dmfw.mca.gov.cn && python update.py
    cd admin/citypopulation.de && python build.py
    cd admin/dsec.gov.mo && python update.py

生成仓库表的脚本写到各自目录的 `.output/`，跟已校订的顶层 CSV 分开；核对后再覆盖过去。

- `dmfw.mca.gov.cn/update.py`：抓内地省市区县和乡镇街道；`--no-mark` 不标名称，
  `--fresh` 清缓存重抓。
- `citypopulation.de/fetch.py`：通用抓取器，给一个 citypopulation.de 的行政区列表页，
  输出页面原始字段（`parent,status,name,native`）。全国、国外的页面都能抓，不套仓库格式。
- `citypopulation.de/build.py`：把台湾、香港页面转成仓库格式；码值和同级顺序取自
  `../huiruisoft.com/`，`--refresh-order` 重建 `.cache/order.json`。澳门页面的口径
  跟 DSEC 不同，不在这个脚本里。
- `huiruisoft.com/update.py`：通用抓取器，站点覆盖全国（省、市、县、乡镇逐级）和台港澳：
  `110000` 北京市、`11/110101` 东城区、`2022` 全国县及以上汇总、`710000` 台湾省。
  按页面表头输出 CSV（区划代码、中文名称、电话区号…），不套仓库格式。
- `dsec.gov.mo/update.py`：按 DSEC《統計年鑑》表 1.1「統計分區」生成澳门表，
  26 个分区的编号和名字写在源码里。

如有需要可以去看看 [https://github.com/montmorill/data_location](https://github.com/montmorill/data_location)，该仓库包含了更多数据。

## 关于 `name` 的格式

`name` 由专名部分（民族部分使用 `|` 标记）与通名部分用 `:` 连接而成，如：

- `北京:市`、`津市:市`、`辉县:市`，
- (清远市) `清新:区`、`浦东:新区`，
- (阳泉) `矿:区`、`峰峰:矿区`、`神农架:林区`、`六枝:特区`，
- `青龙|满族:自治县`、`威宁|彝族|回族|苗族:自治县`，
- `|东乡族:自治县`、`|鄂伦春:自治旗`、`|鄂温克族:自治旗`（此三县(旗)无地名，直接由“民族名+自治县(旗)”构成），
- `龙胜|各族:自治县`、`隆林|各族:自治县`（“各族”在这里指的是多个民族）。
