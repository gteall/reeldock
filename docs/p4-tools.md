# P4 电视剧与媒体工作台

P4 沿用电影的资产上传器、字幕判定、manifest 和归档核对器。新增的剧集模块只负责剧 / 季 / 集身份、TMDB 元数据和资产计划。启动与凭证配置见 [开发指南](development.md)，当前证据见 [进度](progress.md)和[兼容性](compatibility.md)。

## 目录与匹配

```text
待刮削/
  Example (2020)/
    tvshow.nfo
    poster.jpg
    fanart.jpg
    .actors/Actor_One.jpg
    season01-poster.jpg
    season-specials-poster.jpg
    Season 01/
      Example.S01E01.mkv
      Example.S01E01.nfo
      Example.S01E01-thumb.jpg
      Example.S01E01.en.srt
    Specials/
      Example.S00E01.mkv
```

剧根目录可直接放单集，也可包含 `Season 01`、`S01`、`第一季`、`Specials` / `特别篇` 等季目录。支持 `S01E01`、`1x01`、`第一季第二集`；仅有 `E01` 时需要明确季目录，剧根目录中的 `第N集` 按第 1 季解释。季目录与文件名矛盾、重复映射、未知编号会阻塞，不能猜测成任意一集。多集单文件（例如 S01E01E02、S01E01-E02）明确待确认，人工修正也不能把它强行映射为一集。没有任何剧集结构或编号线索的目录仍按电影识别，先规范目录名称再扫描。

扫描只有 PROPFIND，至少两次相同快照且满足稳定窗口后才能启动。剧级匹配优先人工 TMDB ID、目录中的明确 TMDB ID、`tvshow.nfo` 的 TMDB ID，其次名称 / 年份候选；冲突或低置信度待确认。各集继承剧详情的 `original_language`，不会使用单集字段或中文译名分类。只要求当前实际文件对应集数，不等待 TMDB 中尚未下载的集。尚不支持替代剧集组、DVD / 绝对集数映射和 TV IMDb 自动查找。

季集表中的“修正编号”有上下文版本校验；活跃包和已形成归档意图的包禁止改映射。保存后重新扫描确认稳定，再启动新任务。已有 NFO 的 ID、季集编号和人工观看进度受保护；修正后若 NFO 不一致会阻塞，需人工处理远端原文件，软件不会覆盖它。

升级会自动应用 Alembic `0004`。已有 P1–P3 数据保留，首次 P4 扫描使旧源快照重新等待稳定；旧版把季目录登记成包的记录会标记 `superseded` 并重新归入剧包。有活跃租约或未决 MOVE 时拒绝重组，先恢复原任务。升级前备份本地数据库、master.key 和缓存。

## 必需项、增强项和整包门禁

必需项是 `tvshow.nfo`、当前每集同名 NFO、剧级 poster / fanart，以及演员策略所要求的 `.actors` 图片。整包必需集合冻结并逐项上传、完整读回验证后，字幕 handler 还会重新核对这些小资产，之后才允许首次探测 / 射手指纹 / 字幕请求。基础 NFO 省略未知 streamdetails，不依赖探测。已有文件会校验及复用，保留原始字节和人工观看进度。

季海报、逐集 `-thumb.jpg` 是独立增强项，来源无图和请求 / 下载失败分别显示；不会冒充成功，也不阻塞必需集合。可在详情侧栏单独重试一个资产，包括归档后补齐增强项；不重发 MOVE，不重跑字幕，不重写历史 manifest。归档后的补图属于后续资产更新，历史 manifest 仍记录当时归档的快照。源身份或连接配置版本变化时，局部重试也会拒绝继续。

字幕和 MOVE 使用同一个电影处理组件：中文剧所有集 `skipped_tmdb_chinese`，实际音轨显示未探测；未知剧级语言待确认；明确非中文逐集检查实际默认音轨、完整内置简体或任意语言有效匹配外挂。需要下载时走射手四块指纹、格式 / 时间轴和完整上传校验，外挂放在对应视频旁。候选描述明确指向另一季集时拒绝。任一集失败，整个剧目录留在待刮削目录；补齐字幕后“重试失败集”复用其他集及基础资产，但会重新核对已有验证证据。

目标存在同名剧目录时自动归档拒绝冲突，不能增量并入已有剧目录。新增后续季 / 集也不能自动合并到先前归档目录。无自动跨驱动整视频搬运、无盲删、无覆盖。

## 工作台和事件

目录树展开到剧 / 季 / 集，列表支持类型、名称 / 路径、失败、缺字幕 / 待检查筛选。展开剧和单集可看 NFO、缩略图、实际轨道与字幕证据；详情侧栏提供图片、NFO、字幕预览、演员完成数量、人工匹配和资产局部重试。多选可批量刮削 / 暂停 / 继续 / 重试，结果逐项报告拒绝，不能把部分成功显示成全部成功。单项暂停仍可在任务中心操作。

SSE `/api/events/stream` 认证后按持久化事件 ID 重放，支持 `Last-Event-ID` 和 `after`；恢复数据库导致游标超前时发送 reset。浏览器断线时有界退避重连并重新读取 REST 数据，另有 30 秒轮询兜底；重复 / 旧连接事件不能覆盖数据库状态。退出或会话过期会终止流。反向代理需允许 SSE、关闭响应缓冲，避免过短的读取超时。

## 可重复验证

从仓库根目录执行，先完成 `uv sync --frozen`：

```bash
uv run python scripts/p4_verify.py mock --output reports/p4-mock.json
uv run python scripts/p4_verify.py tmdb --output reports/p4-tmdb.json
uv run python scripts/p4_verify.py webdav --output reports/p4-webdav.json
# 仅恢复已有专用测试意图；路径使用上次输出的 local_journal
uv run python scripts/p4_verify.py reconcile --journal reports/p4-runs/.reeldock-p4-实际ID --output reports/p4-webdav-recovered.json
pnpm --dir frontend test
pnpm --dir frontend build
```

- Mock 模式包含解析、工作流、API、TMDB / 射手协议、SSE 和真实本机 HTTP 端到端测试。HTTP 与已有 FFprobe 网关测试需要允许绑定回环端口。测试仅用生成素材和内存存储。
- TMDB 模式从忽略的 `.env.p2` 读取 `P2_TMDB_TOKEN` / `P2_PROXY_URL`，默认只读剧 1399、第 1 季第 1 集及四类图片；可通过 `--series-id` 指定其他剧。不访问 WebDAV 或视频。
- WebDAV 模式复用 P3 验证工具，从 `.env.p0` 读取已约定 `P0_TEST_ROOT`。仅在 `.reeldock-p4-随机ID` 子目录放两份极小的生成字节，使用 Mock 中文 TMDB、真实 WebDAV 上传 / 读回 / manifest / MOVE。视频内容读取被禁止。它证明测试范围内的目录处理，不证明真实剧匹配或大媒体探测；原目录和本地恢复记录保留，不执行 DELETE。
- reconcile 模式仅接受已有 P4 专用测试日志，校验它仍在已约定测试父目录，复用归档核对器。远程 PUT / MKCOL / MOVE / Range 全部拒绝，只读目标并持久化本地恢复结果，不创建新移动意图。
- 缺凭证标为 `unverified` 并非零退出，失败输出安全错误码，不打印第三方正文、私有路径或凭证。报告、数据库、缓存、字幕和截图均被 Git 忽略。

隔离界面样例：

```bash
uv run python scripts/p4_demo.py --data-dir /tmp/reeldock-p4-demo --port 8024
```

必须使用全新目录，随机测试登录信息在该目录的 `ui-login.json`（仅本机，0600）。浏览器登录后扫描两次，批量启动一个中文电影、一个中文剧和一个英语剧。英语剧第 2 集故意无字幕，先观察整包阻塞；在本机创建 `/tmp/reeldock-p4-demo/repair-subtitles` 标记后重试，Mock 存储才提供原创英文外挂，验证恢复。样例所有 Provider 均为内存实现，不导入真实 NAS 配置；进程结束时远端样例消失。

本次没有把 Kodi 新剧集导入 / 刷新 / 真正断网、大型真实片源、飞牛部署或真实 NAS 故障注入标为通过。Kodi 布局依据[剧 NFO 约定](https://kodi.wiki/view/NFO_files/TV_shows)和[演员图片目录约定](https://kodi.wiki/view/Artwork_types)；API 依据 TMDB [剧](https://developer.themoviedb.org/reference/tv-series-details)、[季](https://developer.themoviedb.org/reference/tv-season-details)、[集](https://developer.themoviedb.org/reference/tv-episode-details)接口。
