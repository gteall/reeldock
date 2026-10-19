# ReelDock · 影坞

面向 NAS 的影视原地刮削工具：从 OpenList 提供的一个或多个 WebDAV 目录发现影视文件，通过 TMDB 获取元数据，将 NFO、海报、背景图和所需字幕直接保存在媒体所在目录。演员头像可选，默认关闭；不移动媒体文件或目录。

**当前状态：P5 部署与发布准备。** 已提供生产多阶段镜像、Compose、非 root UID/GID、备份恢复、迁移检查、单实例锁及优雅退出；关于页包含随应用打包的 TMDB 标识。电影与剧集共用基础资产、字幕和最终清单验证流程。当前仍为开发版本，v0.1.0 仅准备发布材料，尚未推送发布镜像、创建标签或 Release。实机与 Mock 证据分别记录在 [开发进度](docs/progress.md)和[兼容范围](docs/compatibility.md)。

生产部署见 [Docker / 飞牛指南](docs/deployment.md)，维护见 [备份、升级与回退](docs/upgrade.md)，复验见 [P5 验证指南](docs/p5-tools.md)。

管理界面的「海报墙」集中展示基础资产已远程验证或已归档的电影与电视剧，支持片名、演员、导演搜索，类型和刮削完成状态筛选，以及片名、年份和评分排序。点击海报进入独立详情页，查看简介、评分、演职人员、随片资产预览、字幕策略与刮削结果；剧集可逐集查看。详情链接支持刷新和浏览器前进、后退，图片通过已登录的本地缓存接口读取。

本机快速开始：`uv sync --frozen` → `uv run reeldock init-admin`；在 frontend 执行 `pnpm install --frozen-lockfile && pnpm build`，再从仓库根目录运行 `uv run uvicorn reeldock.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log`。访问 `http://127.0.0.1:8000`。完整环境、容器及测试步骤见 [开发指南](docs/development.md)。

OpenList 可保持 **302 直链模式**。“下载重定向主机限制”默认留空，自动跟随服务返回的下载地址，无需填写 115 下载域名；填写列表才限制跳转主机。跨域不会携带 WebDAV 账号密码或 Cookie。

## 产品约定

- 名称：ReelDock，中文名：影坞。
- 首要播放端：**Kodi**。
- 元数据首期仅使用 TMDB，字幕首期仅使用射手 API；分别通过 Provider 接口为后续扩展预留空间。
- 流程固定为：扫描目录 → 匹配 TMDB → 基础 NFO / 图片 / 演员资产刮削、上传及读回验证 → 字幕阶段 → 最终清单验证 → 完成。单次扫描通过即可刮削，无需等待或二次扫描。
- 字幕阶段先检查 TMDB 详情的 `original_language`（默认中文集合 `zh`、`cn`）。命中中文则跳过音轨 / 字幕探测与字幕下载；这是本项目按语言分类的业务规则。
- 只有 TMDB 非中文的影视才进入实际音轨 / 字幕探测；默认音轨非中文、无确认有效的完整内置简体中文字幕、也无有效外挂字幕时，必须补齐外挂字幕才能完成刮削。
- TMDB 语言缺失 / 无法识别时待确认；`language=zh-CN` 请求参数和中文标题不作为跳过探测的证据。电视剧使用剧级 `original_language`。
- 按已确认的需求，**已有外挂字幕不限制语言**，但必须有效且能关联到当前视频。自动下载优先简体中文。
- 可配置多个刮削目录，产物原地保存；重叠目录自动避免重复扫描，现存 NFO / 图片不盲目覆盖。
- 演员头像刮削默认关闭，开启后按演员范围与头像策略保存到 `.actors/演员名.jpg`。关闭时仍保留演员姓名和角色；界面使用服务端缓存。
- 管理员密码最少 8 位；已有账户与密码保持有效。
- 目标部署方式：飞牛 NAS 上使用 Docker Compose，浏览器访问管理界面。

## 技术方案

| 部分 | 实现选型 |
| --- | --- |
| 后端 | Python 3.12 + FastAPI + Pydantic |
| 前端 | React + TypeScript + Vite + Ant Design + TanStack Query |
| 持久化 | SQLite WAL + SQLAlchemy 2 + Alembic，数据库放 NAS 本地磁盘 |
| 任务 | SQLite 持久化任务、租约、阶段检查点；单实例有界异步执行 |
| 远程访问 | HTTPX；WebDAV PROPFIND / GET Range / PUT |
| 媒体探测 | FFprobe；必要时限额使用 FFmpeg 读取文本字幕 |
| 交互 | REST API + SSE 进度推送 |
| 发布 | 单容器提供 API、Worker 和静态前端；GitHub Actions + GHCR |

## 设计与开发

- [P0 工具运行说明](docs/p0-tools.md)
- [本机开发与容器启动](docs/development.md)
- [P2 电影刮削与验证](docs/p2-tools.md)
- [P3 字幕策略（历史指南）](docs/p3-tools.md)
- [P4 剧集与批量工作台](docs/p4-tools.md)
- [Docker / 飞牛部署](docs/deployment.md)
- [备份、升级与回退](docs/upgrade.md)
- [P5 验证指南](docs/p5-tools.md)
- [v0.1.0 发布说明草案](docs/releases/v0.1.0.md)
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

本仓库仅打包获批准的 TMDB Logo，不包含影视图片、用户片源、第三方下载字幕或访问凭证；验证媒体与字幕由代码生成。标识来源见 [来源说明](docs/attribution.md)。

当前原地刮削配置与升级行为见 [原地刮削说明](docs/in-place-scraping.md)。P0–P5 文档中的归档 / MOVE 内容为历史设计与验证记录，当前版本已移除该功能。
