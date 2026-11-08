# osu-mapper-graph

从初始 mapper 开始，遍历所有 Ranked osu!standard 谱面集中的 host、guest difficulty 和 collab owners，直到没有新 mapper。数据写入 SQLite，可中断后继续，并导出为 CSV。每个 osu!standard difficulty 的 User tags 和投票数也会保存。

## osu 登录与凭据

运行爬虫**不需要 osu 用户网页登录，也不需要个人 OAuth token**。它使用 osu! API v2 的 `client_credentials` + `public` scope。

需要先登录 osu! 网站创建一个 OAuth 应用，拿到应用级 `client_id` 和 `client_secret`，然后只在当前 shell 设置环境变量：

```bash
export OSU_CLIENT_ID='...'
export OSU_CLIENT_SECRET='...'
```

不要把这两个值写进仓库、命令历史或提交记录。

## 安装

```bash
cd /home/wangxianming/dev/osu-mapper-graph
uv sync
```

## 运行

默认初始 mapper 是 `Sotarks`：

```bash
uv run osu-mapper-graph crawl
```

测试连接和小范围遍历可以限制深度：

```bash
uv run osu-mapper-graph crawl --max-depth 1
```

深度从 seed 的 `0` 开始；`--max-depth N` 会展开深度小于 `N` 的 mapper，并保留边界 mapper 为 pending。之后去掉深度限制继续运行时，不会漏掉这些边界节点。

增加初始 mapper 或恢复失败节点：

```bash
uv run osu-mapper-graph crawl --seed Sotarks --seed another_mapper --retry-failed
```

如果数据库是在 User tags 功能加入前创建的，使用下面的选项重新抓取已有 mapset 的 User tags：

```bash
uv run osu-mapper-graph crawl --refresh-user-tags
```

查看进度并导出总表：

```bash
uv run osu-mapper-graph status
uv run osu-mapper-graph export --output data/mapper_catalog.csv
```

对所有至少拥有一个 User tag 的 Ranked osu!standard difficulty 计算 Stable 模式的 NM PP（需要本地 `.osu` 文件）：

```bash
uv run osu-mapper-graph estimate-pp --output data/mapper_catalog_with_pp.csv
```

该命令读取本地 `.osu` 文件，使用 `rosu-pp-py` 计算 100%、99%、98%、97%、96%、90% 以及对应 0/1/2/3/5/10 miss 场景。每个场景会枚举达到目标准确率的最近整数判定分布，只保留 Stable PP 最大值 `stable_best`，并保存命中数和 aim/speed/accuracy/flashlight 分项。结果会断点写入 SQLite，重新运行只处理未成功的记录。

本机最终数据集和扫描缓存发布在 GitHub Release 中，文件说明见 [ARTIFACTS.md](ARTIFACTS.md)。

导出与某次搜索深度严格对应的表（例如 Sotarks + 一层延伸）：

```bash
uv run osu-mapper-graph export --max-depth 2 --output data/mapper_catalog_depth2.csv
```

程序使用 `user_id`、`beatmapset_id`、`beatmap_id` 去重，不依赖用户名或 difficulty 名称猜测 guest。每个 difficulty 的 `owners` 都会保存，因此同一难度的 collab 多作者不会丢失。CSV 中的 `user_tag_names` 是标签名，`user_tag_votes` 是 `标签:票数`。
