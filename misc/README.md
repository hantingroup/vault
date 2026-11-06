# 汉听大典/杂项

各种名单、词表和字典，以及不好归到其他目录的数据集。

| 文件 | 内容 |
| - | - |
| `一百单八将.csv` | 水浒传一百单八将的座次、星宿名、绰号、姓名 |
| `五十六民族.csv` | 中国 56 个民族名称 |
| `百家姓.csv` | 百家姓姓氏 |
| `通用规范汉字表.txt` | 通用规范汉字表，按一级、二级、三级各占一行 |
| `平水韵.json` | 平水韵，平上去入四声下按韵部列字 |
| `切韵.json` | 切韵，按声母列字 |
| `千字文.txt` | 千字文正文 |
| `七十二名泉.csv` | 济南七十二名泉，列为 `name,type`（泉、池、潭、湾、井） |
| `名泉碑.csv` | 名泉碑所列泉名，列为 `place,name,alias` |
| `氨基酸.csv` | 20 种标准氨基酸的简写、缩写、英文、中文和性质 |
| `人体骨骼.json` | 人体骨骼层级，分中轴骨和附肢骨 |
| `雷达站.json` | 各省级行政区（含港澳台）的雷达站名单 |

## arknights

`arknights/characters.csv` 是明日方舟干员表，数据来自 [prts.wiki](https://prts.wiki/)
的 Cargo 导出，随游戏版本更新。

    cd misc/arknights/scripts && python update.py

`update.py` 用 Playwright 过掉站点的 Anubis 挑战后下载导出的 CSV，反转义后写回
`../characters.csv`；`--from results.csv` 只处理已下载的原始 CSV。
`fetch.http` 是手动导出的兜底方案。
