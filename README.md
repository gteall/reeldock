# ReelDock · 影坞

面向 NAS 的影视刮削与归档工具：从 OpenList 提供的 WebDAV 待刮削目录发现影视文件，通过 TMDB 获取元数据，将 NFO、海报、背景图、演员头像和所需字幕写回 WebDAV，全部必需项验证通过后移动整个媒体目录。

**当前状态：P4 电视剧与媒体工作台已实现。** 电影与剧集共用上传、字幕及安全归档组件；整包基础必需资产验证后才逐集处理字幕，中文剧全部零探测。支持季集人工修正、逐集 NFO、共享图片与 `.actors`、独立增强图、失败集恢复、目录树、批量操作及 SSE 重连。目标已有剧目录时拒绝合并 / 覆盖。自动归档默认关闭，需确认存储范围能力。证据与边界见 [开发进度](docs/progress.md)、[P4 指南](docs/p4-tools.md)及[兼容性记录](docs/compatibility.md)。

本机快速开始：`uv sync --frozen` → `uv run reeldock init-admin`；在 frontend 执行 `pnpm install --frozen-lockfile && pnpm build`，再从仓库根目录运行 `uv run uvicorn reeldock.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log`。访问 `http://127.0.0.1:8000`。完整环境、容器及测试步骤见 [开发指南](docs/development.md)。

OpenList 可保持 **302 直链模式**。“下载重定向主机限制”默认留空，自动跟随服务返回的下载地址，无需填写 115 下载域名；填写列表才限制跳转主机。跨域不会携带 WebDAV 账号密码或 Cookie。

## 产品约定

- 名称：ReelDock，中文名：影坞。
- 首要播放端：**Kodi**。
- 元数据首期仅使用 TMDB，字幕首期仅使用射手 API；分别通过 Provider 接口为后续扩展预留空间。
- 流程固定为：扫描并等待稳定 → 匹配 TMDB → 基础 NFO / 图片 / 演员资产刮削、上传及读回验证 → 字幕阶段 → 最终清单验证 → 归档。
- 字幕阶段先检查 TMDB 详情的 `original_language`（默认中文集合 `zh`、`cn`）。命中中文则跳过音轨 / 字幕探测与字幕下载；这是本项目按语言分类的业务规则。
- 只有 TMDB 非中文的影视才进入实际音轨 / 字幕探测；默认音轨非中文、无确认有效的完整内置简体中文字幕、也无有效外挂字幕时，必须补齐外挂字幕才能归档。
- TMDB 语言缺失 / 无法识别时待确认；`language=zh-CN` 请求参数和中文标题不作为跳过探测的证据。电视剧使用剧级 `original_language`。
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

- [P0 工具运行说明](docs/p0-tools.md)
- [本机开发与容器启动](docs/development.md)
- [P2 电影刮削与验证](docs/p2-tools.md)
- [P3 字幕与安全归档](docs/p3-tools.md)
- [P4 剧集与批量工作台](docs/p4-tools.md)
- [兼容性与实测边界](docs/compatibility.md)
- [开发进度与交接](docs/progress.md)
- [总体架构与业务规则](docs/architecture.md)
- [分阶段开发计划](docs/roadmap.md)
- [各阶段 vibe coding 提示词](docs/vibe-coding-prompts.md)
- [验收标准与故障场景](docs/acceptance.md)
- [接口调研与验证记录](docs/research.md)

预估一个熟悉 Python / React 的开发者全职投入约 22 个工作日，含集成缓冲约 5–6 周。实际排期在 P0 验证实际存储和媒体样本后调整。

## 外部服务

部署时需自行配置 TMDB API 凭证、OpenList 地址和 WebDAV 账号。凭证仅在服务端保存，不提交到仓库。关于页面按 [TMDB 要求](https://developer.themoviedb.org/docs/faq)提供来源标识、批准的 Logo 和声明：

> This product uses the TMDB API but is not endorsed or certified by TMDB.

本仓库不包含 TMDB 素材、用户片源、第三方下载字幕或任何访问凭证；P0 测试只使用代码生成的原创媒体与字幕。

P3 已接入基础门禁后的字幕策略、最终清单与安全归档，使用与复验见 [P3 指南](docs/p3-tools.md)。自动归档默认关闭，需确认对应存储范围的 MOVE 能力；真实测试仅操作专用目录。
