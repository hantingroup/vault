# 汉听大典/铁路

数据按来源分目录，目录名就是来源域名：

| 目录 | 来源 | 数据文件 |
| - | - | - |
| `12306/` | [中国铁路 12306](https://mobile.12306.cn/otsmobile/h5/otsbussiness/trainPrefix/trainPrefix.html)「全国铁路列车车次编排公告」 | `stations.csv`、`trains.csv` |

    railway/
      12306/
        stations.csv   车站电报码表
        trains.csv     第一批新旧车次对照

- `stations.csv`：车站电报码表，取自页面内嵌的 `COMMON_DATA_12306.station`。
  列为 `value`（站名）、`id`（电报码）、`city_name`（所属城市）、`py_code`（全拼）、
  `first_code`（首字母）。`city_name` 为空表示来源没有给出所属城市。
- `trains.csv`：新旧车次对照，取自页面「车次查询」内嵌的数据。
  列为 `old`（旧车次）、`src`（始发站）、`dest`（终到站）、`new`（新车次）。

## 车次编排

按 2026 版《列车车次编排规定》，普速旅客列车使用 Z、N、V 字头，普速旅游列车使用
Y 字头；动车组列车沿用 X、G、T、K、D、C 等字头。

`trains.csv` 是第一批车次变更，只涉及普速旅客列车，自 11 月 25 日 0 时起实施。
新车次的开行区段、运行时刻基本保持不变。动车组列车车次本次不变，计划 12 月下旬实施。

---

数据收集于 2026 年 11 月。
