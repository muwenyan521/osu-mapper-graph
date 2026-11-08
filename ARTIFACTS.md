# 发布材料

本项目的源码、测试和 `tools/attributes-pp` 适配器直接位于仓库中。体积较大的扫描结果不放进 Git 历史，而是作为 GitHub Release assets 发布。

## 最终数据

Release [`data-2026-11-08-final`](https://github.com/muwenyan521/osu-mapper-graph/releases/tag/data-2026-11-08-final) 包含：

- `mapper_catalog_full_pp.csv`：最终平面表，551,295 行；PP JSON 只保留 Stable `stable_best`。
- `mapper_graph.sqlite3`：完整可继续查询的 SQLite 图数据库，4,864 条成功 PP 记录。
- `stable_pp_difference_report.csv`：在精简前对 `stable_best`、`stable_median`、`stable_worst` 的差异审计报告，共 3,648 个有差异的场景，涉及 460 张谱面。
- `osu-mapper-graph-additional-data.tar.zst`：其余非备份材料，包括其他 CSV/XLSX/JSON/TXT 表格和本机保存的 4,864 个 `.osu` 文件。

归档中保留了历史试跑和对比表，便于复核；带有 `backup-` 的数据库/CSV 快照和 Rust/Python 构建缓存没有上传。

## 计算范围

- 仅 `osu!standard`、`ranked` difficulty。
- 图遍历保存 host、guest 和 collab difficulty owners，并保存 User tags 与票数。
- PP 场景使用 Stable 模式；每个场景枚举最近的整数 `n300/n100/n50` 分布，只写入 PP 最大的 `stable_best`。
- 运行所需 osu! API 凭据只通过 `OSU_CLIENT_ID` 和 `OSU_CLIENT_SECRET` 环境变量提供，不写入仓库或发布资产。

## 校验

Release 描述中列出每个资产的 SHA-256。下载后可用 `sha256sum` 校验；SQLite 可用 `sqlite3 mapper_graph.sqlite3 '.tables'` 打开，CSV 可直接用 DuckDB、Polars 或表格工具读取。

当前资产 SHA-256：

```text
63772b121b8d26ac7b3d3a3da7bcce1bbd91470d85a9e5b5e3a065ee668e3e82  mapper_catalog_full_pp.csv
eabdd617d91da86eeaeec93edaff2ad2f2e9f7f9c69a51f43eda5f7b4d1936c5  mapper_graph.sqlite3
d78945909b0f6bc72f4b7d725c9c56cac66c038b3d87f413022d0baad10918ea  stable_pp_difference_report.csv
7bf9e18cbe9955b29c305c36be8145b19c2b7d33f3a7d8338720bcb3dfb023bb  osu-mapper-graph-additional-data-20261108.tar.zst
```
