# ReelDock · 影坞

面向 NAS 的影视刮削与归档工具：从 OpenList 提供的 WebDAV 待刮削目录发现影视文件，通过 TMDB 获取元数据，将 NFO、海报、背景图、演员头像和所需字幕写回 WebDAV，全部必需项验证通过后移动整个媒体目录。

**当前状态：方案设计阶段。** 本仓库目前包含架构、开发计划和验收标准，尚无可运行的应用或 Docker 镜像。已完成一次射手 API 公开样本的查询及下载验证；实际 NAS、OpenList 存储驱动和 Kodi 联调纳入 P0。

## 产品约定

- 名称：ReelDock，中文名：影坞。
- 首要播放端：**Kodi**。
- 元数据首期仅使用 TMDB，字幕首期仅使用射手 API；分别通过 Provider 接口为后续扩展预留空间。
- 当默认音轨为非中文、没有确认有效的内置简体中文字幕、也没有有效外挂字幕时，必须补齐外挂字幕才能归档。
- 按已确认的需求，**已有外挂字幕不限制语言**，但必须有效且能关联到当前视频。自动下载优先简体中文。
- 只有获取、生成、上传、读回验证全部成功，且源目录稳定、归档无冲突时，才能执行目录移动。
- 演员头像默认保存为媒体目录中的 `.actors/演员名.jpg`，适配 Kodi；管理界面使用 ReelDock 缓存，不要求浏览器直连 TMDB。
- 目标部署方式：飞牛 NAS 上使用 Docker Compose，浏览器访问管理界面。

## 技术方案

| 部分 | 计划选型 |
| --- | --- |
| 后端 | Python 3.12 + FastAPI + Pydantic |
| 前端 | React + TypeScript + Vite + Ant Design + TanStack Query |
| 持久化 | SQLite WAL + SQLAlchemy 2 + Alembic，数据库放 NAS 本地磁盘 |
| 任务 | SQLite 持久化任务、租约、阶段检查点；单实例有界异步执行 |
| 远程访问 | HTTPX；WebDAV PROPFIND / GET Range / PUT / MOVE |
| 媒体探测 | FFprobe；必要时限额使用 FFmpeg 读取文本字幕 |
| 交互 | REST API + SSE 进度推送 |
| 发布 | 单容器提供 API、Worker 和静态前端；GitHub Actions + GHCR |

## 设计与开发

- [总体架构与业务规则](docs/architecture.md)
- [分阶段开发计划](docs/roadmap.md)
- [验收标准与故障场景](docs/acceptance.md)
- [接口调研与验证记录](docs/research.md)

预估一个熟悉 Python / React 的开发者全职投入约 22 个工作日，含集成缓冲约 5–6 周。实际排期在 P0 验证实际存储和媒体样本后调整。

## 外部服务

部署时需自行配置 TMDB API 凭证、OpenList 地址和 WebDAV 账号。凭证仅在服务端保存，不提交到仓库。关于页面按 [TMDB 要求](https://developer.themoviedb.org/docs/faq)提供来源标识、批准的 Logo 和声明：

> This product uses the TMDB API but is not endorsed or certified by TMDB.

本仓库不包含 TMDB 素材、用户片源、字幕样本或任何访问凭证。
