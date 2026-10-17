# ReelDock · 影坞：开发进度与交接

更新日期：2026-10-17。

## 当前状态

P5 部署与发布准备已实施：生产多阶段 Docker / Compose、非 root UID/GID、健康检查、日志轮转、优雅退出、独立本地数据库/密钥与可重建缓存；新增单实例锁、迁移前检查、在线备份和空目录恢复、TMDB 关于页与手动发布工作流。版本仍为开发版，未发布镜像/标签/Release。验收证据与真实/Mock 边界见下方 P5 记录；完整真实媒体链路、Kodi 无缓存断网及大媒体仍待验。

已完成：

- 创建公开 GitHub 仓库并提交初始方案。
- 在本机用公开测试哈希完成一次射手候选查询和字幕下载格式验证，详见 [调研记录](research.md)。
- 按最新需求统一流程：先匹配 TMDB、完成基础资产上传验证，再按 original_language 分流字幕；中文作品跳过探测。
- 编写 [分阶段 vibe coding 提示词](vibe-coding-prompts.md)和更新验收矩阵。
- 实现配置驱动的 [P0 工具](p0-tools.md)、回环 HTTP 故障服务器、自建媒体 / Kodi 样例生成器、JSON 报告与正确退出码。
- 真实 WebDAV：目录扫描、特殊字符文件名 PUT / SHA-256 读回、远程四块指纹与本地一致、目录 MOVE 与只读意图恢复核对、目标冲突保护均通过。
- 自建四种媒体经真实 WebDAV / CDN 的有预算 FFprobe 均通过；真实用户片源未读取。
- 本机 Kodi 21.3 / Estuary：基础 NFO 首次入库、海报 / 背景 / .actors JPG、修改 NFO 后刷新通过；没有断开外网，不把本地信息模式当作断网证据。
- 添加 GitHub Actions 离线回归工作流，完成 44 项本机自动化测试。兼容边界见 [compatibility.md](compatibility.md)。
- 实施 P1 FastAPI / React 应用、uv / pnpm 锁文件、Alembic 首次迁移、加密配置、管理员登录与来源 / CSRF 校验。
- 实施数据库任务领取、包租约 / 心跳及代次校验、幂等键、检查点、退避重试、暂停 / 继续与启动恢复；显式建立基础资产 → 字幕 → 最终清单 → 归档门禁。
- 完成 P2 后端 101 项、前端 4 项测试、wheel / 静态资源 / 开发镜像构建和 9 项容器持久化烟测；真实 TMDB 与专用 WebDAV 基础资产上传通过。
- 通过 P1 后端 65 项、前端 4 项、P0 回归 44 项测试；完成后端 wheel、前端静态资源和本机开发镜像构建，容器重启持久化烟测通过。
- 完成 P3 字幕策略、射手 Provider、不可变最终清单和安全归档恢复；后端 181 项测试通过，真实专用 WebDAV 中文小包归档及公开射手样本分项通过。

## 阶段状态

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| P0 外部能力验证 | 工具完成，部分实机验收待补 | 离线回归及真实 WebDAV / 射手 / Kodi 导入刷新已通过；断网、大型片源和真实故障待验 |
| P1 工程基础 | 已完成本阶段验收 | 本机 / 开发容器启动、配置与任务持久化、竞争领取 / 恢复测试通过；实际刮削及 MOVE 关闭 |
| P2 电影基础刮削 | 已完成实现与 Mock 验收，真实分项通过 | 真实 TMDB、WebDAV 资产上传通过；真实电影包、P2 Kodi / 飞牛未验证 |
| P3 字幕与归档 | 已实现，Mock / 本机媒体验收通过 | 真实射手分项通过；专用 WebDAV 验证记录见下文，生产路径 / 大媒体 / 真正断网 Kodi 未验证 |
| P4 电视剧与完整 UI | 已实现，Mock / 本机端到端通过 | 真实 TMDB 分项通过，专用 WebDAV 恢复记录见下文；真实剧 / Kodi / 飞牛未验证 |
| P5 NAS 发布准备 | 已实现，实机分项通过；完整验收未完成 | NAS Compose 与 17 项容器验收通过；Kodi 断网/真实媒体完整链路待验，尚未发布 |

## 最新业务约束

- 整包基础 NFO / 图片 / 演员必需资产先全部远程验证，才能进入字幕阶段。
- TMDB original_language 默认中文集合 zh / cn，命中后 skipped_tmdb_chinese，零视频探测、零射手哈希和请求。
- TMDB 非中文才探测实际音轨 / 字幕；已有有效外挂不限语言。
- 基础 NFO 无 FFprobe 依赖，未探测 streamdetails 省略。
- 最终 manifest 和安全移动仍需通过，不因中文跳过字幕而跳过归档校验。

## 下一步

部署见 [deployment.md](deployment.md)，复验见 [p5-tools.md](p5-tools.md)，备份/升级/回退见 [upgrade.md](upgrade.md)。继续补齐真实媒体、Kodi 断网无缓存、大媒体和真实故障注入验收；条件 PUT 非原子，媒体包仍须独占管理。明确发布指令前保持开发版本和草稿 PR，不推送 GHCR、不打 Git 标签、不创建 Release。


## P0 实施记录：2026-10-08

分支：沿用 main。交付 `scripts/p0.py`、`scripts/p0lib/`、`tests/test_p0.py`、`.env.example`、P0 CI、工具说明及兼容性文档；本次提交不包含 `.env.p0`、reports、fixtures、真实片源、字幕或签名链接。

实际运行记录：

| 命令 / 操作 | 结果与证据范围 |
| --- | --- |
| `python3.12 -m unittest discover -v` | 44 项通过：特殊路径、逐项权限、上传损坏、Range 200 / 错范围 / 短读、重定向凭证与条件头、MOVE 超时 / 207 / 冲突、射手空结果 / 网络错误、预算、Kodi NFO/JPG、CLI 退出码 |
| `python3.12 scripts/p0.py offline --output reports/p0-offline.json` | 25 passed / 0 failed / 4 unverified，退出 2；真实 FFmpeg / FFprobe + 本机 Mock HTTP，外部环境保留未验证 |
| `python3.12 scripts/p0.py live --env-file .env.p0 --allow-write --media-matrix --shooter --output reports/p0-live.json` | 最终复验 11 passed / 0 failed / 3 unverified，退出 2；真实 NAS 服务 / CDN、公开射手样本；未验证项包括真实故障、大型片源和本次命令未覆盖的 Kodi |
| `python3.12 scripts/p0.py reconcile --env-file .env.p0 --journal reports/move-intent-<实际ID>.json --output reports/p0-reconcile.json` | 使用实际保存意图，1 passed / 0 failed / 0 unverified，退出 0；仅只读核对，不代表注入过 NAS 超时 |
| `python3.12 scripts/p0.py kodi-fixtures` 与本机 Kodi UI | 生成独立样例并添加专用单电影源；首次 NFO / 随片图片导入成功；plot 改为独特文本后刷新成功。保留该测试源，未接入用户实际片库 |
| `python3.12 scripts/p0.py kodi-check --observation fixtures/kodi/kodi-observation.json --output reports/p0-kodi.json` | 5 passed / 0 failed / 1 unverified，退出 2；记录校验加 4 项实际 UI 观察，断网项为 null / unverified；命令本身不自动控制 Kodi |
| `python3.12 -m compileall -q scripts tests`、`git diff --check` | 通过 |

过程中定位并修复 / 记录：网页地址 PROPFIND 返回 405，改用 `/dav/`；115 HTTPS CDN 需要精确主机白名单；DAV ETag 不能转发到 CDN，否则 412；目标冲突返回非标准 500，核对双方完整哨兵后记录安全拒绝，不吞掉一般 500。详情、流量及耗时在 compatibility.md。

所有真实写入都限制在用户指定父目录中新建的 `.reeldock-p0-<随机ID>`，只写自建小文件和极小媒体。未执行 DELETE，失败和成功样例均保留供用户核对及人工清理。账号和实际路径仅存在本地忽略配置 / 意图中；本地配置补全了 `/dav/` 与已核对的下载主机白名单。

## P1 实施记录：2026-10-08 至 2026-10-09

分支：`codex/p1-foundation`；审阅入口：[PR #1](https://github.com/gteall/reeldock/pull/1)。保留 P0 工具，新增 `backend/src/reeldock`、`backend/tests`、`frontend/src`、锁文件、`Dockerfile.dev` / `compose.dev.yaml` / `.devcontainer`、P1 CI，以及 [开发指南](development.md)。

数据库首次迁移 `0001` 覆盖配置、管理员 / 会话 / 登录限流、包、媒体、资产、任务、步骤、事件和归档意图。数据库放本地磁盘，配置经 Fernet 加密，密码用 Argon2id 哈希；管理员通过本机 CLI 初始化。修改接口具有 Origin / CSRF 校验，日志与事件只保留安全代码。当前事件接口为持久化 REST 游标，SSE 入口语义已预留但传输尚未开启。

真实执行的命令 / 验证：

| 命令 / 操作 | 结果与证据范围 |
| --- | --- |
| `uv sync --frozen`、`uv build --wheel` | 后端锁定依赖与 wheel 构建通过，迁移脚本 / 模板包含在 wheel 内 |
| `uv run ruff check backend scripts/p1_*.py`、`uv run ruff format --check backend scripts/p1_*.py` | 通过 |
| `uv run pytest -q` | 65 项通过；配置加密 / 重启、无效路径、来源与 CSRF、幂等并发提交、竞争领取、租约过期和旧持有者拒绝、心跳丢失后调度槽位继续工作、暂停、局部重试与耗尽、前驱检查点 / 阶段顺序、运行中配置变化及上下文失效；含真实子进程非正常退出恢复 |
| `pnpm install --frozen-lockfile`、`pnpm format:check`、`pnpm test`、`pnpm build`（frontend） | 格式、4 项 API 传输测试、TypeScript 与 Vite 构建通过 |
| `python3.12 -m unittest discover -v` | P0 的 44 项回归通过，保留 P0 的专用媒体能力测试与正式流程的区别 |
| `uv run python scripts/p1_check_webdav.py --env-file .env.p0` | 新 HTTPX Provider 在真实 WebDAV 列出扫描目录 2 项、测试父目录 3 项；零视频内容读取、零写入、零 MOVE |
| 本机浏览器实际操作 | 管理员登录、无效输入 / 输出路径被拒绝、正确配置保存与刷新保留、Mock WebDAV 经 Worker 完成任务及检查点展示；截图在忽略的 `reports/p1-ui.jpg` |
| `docker compose -f compose.dev.yaml build` | Docker Hub 鉴权端点最初超时；使用 `NODE_IMAGE` / `PYTHON_IMAGE` 的公共 ECR 镜像来源参数构建通过，未修改全局 Docker 配置 |
| `docker compose -f compose.dev.yaml up -d --no-build` | 本机容器健康检查和静态前端通过，单 Uvicorn 进程，非 root 用户 |
| `uv run python scripts/p1_container_smoke.py` | 独立随机容器 / 数据卷 9 项检查通过；真实重启保留配置、会话、暂停任务和事件；重复提交幂等；仅测试数据，无真实 WebDAV |
| `git diff --check` | 通过；本地凭证、报告、数据库、样例和构建产物不提交 |
| GitHub Actions | P0 回归与 P1 后端 / 前端 / 容器检查通过；最新提交结果见 PR 的 Checks，未发布镜像 |

任务边界：`match_metadata → base_assets_verified → subtitle_policy → final_manifest → archive`。前驱检查点和当前版本远程资产证据都要通过，空集合不能满足基础门禁。失败只重试当前步骤，已完成基础步骤在字幕重试中保留；源快照 / TMDB / 语言 / 策略变化使旧证据失效。P1 没有生产 TMDB、媒体探测或射手 handler；archive 的 Worker 门禁和 WebDAV MOVE 都关闭，PUT 同样尚未开放。只有真实只读连接任务可以执行完成。

剩余边界：真实代理、飞牛 Docker 与容器访问真实 NAS 未验证；VS Code 交互式 Reopen in Container 未实测。P0 的断网 Kodi、大媒体及 NAS 故障待验项保持不变。当前 Ant Design 主块约 1.05 MB / gzip 336 KB，构建体积提示留待后续 UI 拆分；Starlette 对测试客户端 HTTPX 的弃用提示不影响当前锁定依赖结果。没有发布版本或上传镜像。

验证用本机 API、Mock 服务和开发 Compose 容器已停止，避免占用正式开发端口；普通 Compose 数据卷保留。临时 UI 仅使用独立生成式数据，用户 `.env.p0` 未复制进产品数据库或镜像。

下一阶段从 P2 提示词开始，复用 Provider、资产版本与 Worker 检查点；不要启用任何提前探测路径。新配置页面可先保存 TMDB 凭证，P2 再进行真实接口验证。

## P2 实施记录：2026-10-09

分支沿用 `codex/p1-foundation`，沿用草稿 [PR #1](https://github.com/gteall/reeldock/pull/1)，未合并、未发布镜像或版本。

交付 `scanner.py`、`matching.py`、`providers/tmdb.py`、`exporter.py`、`cache.py`、`pipeline.py`，Alembic `0002`，电影 / 匹配 / 资产预览 API，React 电影列表与详情页，P2 验证 / UI fixture 工具、Pillow 锁定依赖，以及 [P2 指南](p2-tools.md)。保留原 P1 登录、配置加密、租约和任务恢复；新 `movie_base` 任务只建立匹配与基础验证两个步骤，不推进字幕。

扫描仅登记目录信息；至少两次稳定窗口观察，源变化 / 下载临时文件 / 多媒体混装 / 疑似电视剧均阻塞。明确 TMDB / IMDb ID 优先，否则采用片名 / 年份评分，歧义需人工确认。TMDB 支持 API Token / Key、中文字段级回退、图片回退、configuration 和共享缓存、有限 429 退避。原始语言独立保存，不被 localized 请求覆盖。

NFO 无探测依赖，未知 fileinfo / streamdetails 省略，头像按 Kodi 的 `.actors` 名称约定保存；默认前 20 位有来源者必需，可选全部 / strict，来源无图和未选择明确区分。已有 ID 一致的 NFO 逐字节保留，包括人工内容和观看进度；解释前缓存原件，冲突 / 无 ID / 坏 XML 不覆盖。有效人工图片可复用；影坞旧 ID 资产不能自动重绑新电影。统一上传器先持久化缓存 / 哈希，PUT 后完整读回比对；包租约 / 上下文与最终源文件信息快照都复核，已验证项与中断后的计划内容可恢复。

实际运行记录：

| 命令 / 操作 | 结果与证据范围 |
| --- | --- |
| `uv sync --frozen`、`uv build --wheel` | 锁定依赖与后端打包通过；新增 Pillow 解码验证，迁移随包交付 |
| `uv run ruff check backend scripts/p1_*.py scripts/p2_*.py`、对应 `ruff format --check` | 通过 |
| `uv run pytest -q` | **101 项通过**；含 P1 回归与 P2 匹配冲突、字段 / 图片回退、429、坏图片、PUT 内容不一致、超时有 / 无目标核对、现存 NFO / 观看进度保护、图片复用、源 / ID / 语言 / 必需集合变更、局部重试与中断恢复、原 P1 数据迁移保留、管理鉴权 / 预览 |
| `uv run python scripts/p2_verify.py mock --output reports/p2-mock.json` | P2 Mock 扫描 → TMDB → NFO / 图片 → 上传验证闭环及故障矩阵通过；P2 测试硬性拒绝视频 / 字幕内容读取、媒体子进程、射手或 MOVE，禁止调用次数为零 |
| `pnpm format:check`、`pnpm test`、`pnpm build`（frontend） | 格式、4 项传输测试、TypeScript 和 Vite 构建通过 |
| `uv run python scripts/p2_verify.py tmdb --output reports/p2-tmdb.json` | 用户本地凭证 + 代理，真实电影 550 的中文详情 / 原始语言 en、configuration、75 位演员、海报 / 背景 / 一个头像下载与解码通过；同次缓存请求复用通过；最初直连超时明确记录，没有输出密钥 |
| `uv run python scripts/p2_verify.py webdav --output reports/p2-webdav.json` | 专用随机子目录内自建 NFO / poster / fanart / .actors 4 项 SHA-256 全量读回通过，客户端拒绝现有目标；零视频内容读取 / 探测 / 射手 / MOVE；这是资产分项验证，非真实电影全链路 |
| OpenList 条件 PUT 能力核对 | **If-None-Match 被忽略，返回 201**。首轮正确报失败；补写前 stat 保护后重测，报告分开记录 client guard passed / server conditional unsupported。仅对同内容自建哨兵测试，未覆盖用户资产 |
| 本机浏览器（隔离 Mock） | 登录、首次扫描等待稳定、第二次稳定、基础刮削完成、资产详情和 NFO 预览通过；明确显示 en、实际音轨未探测、字幕尚未进入；截图 `reports/p2-ui.png` 本地忽略，不当成真实 Kodi 验证 |
| `docker compose -f compose.dev.yaml build --build-arg NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm` | 最终开发镜像构建通过，沿用 P1 已验证的公共镜像源参数；无镜像发布 |
| `uv run python scripts/p1_container_smoke.py` | 最终镜像 9 项隔离烟测通过：健康、静态前端、加密配置、幂等、重启会话 / 配置 / 暂停任务 / 事件、未扫描包真实阻塞于 package_not_stable；仅本机 Mock 配置 |
| P0 非媒体单元回归 | 41 通过 / 3 跳过；通过 unittest 显式跳过整个 MediaTests 类，不运行 P0 的 FFmpeg / FFprobe 专用能力矩阵 |
| `git diff --check` 与提交前敏感内容检查 | 通过；`.env.p0` / `.env.p2`、数据库、报告、素材与构建产物均忽略 |

实际边界与交接：

- OpenList 的条件 PUT 不可靠，客户端存在性检查和包租约只保护影坞自身并发；不能保证与其它程序同时写同一路径时的原子互斥。刮削包须独占管理，详见 compatibility.md。
- 真实 WebDAV 生成了两组专用小资产测试子目录，保留供人工核对，未 DELETE；没有读取用户片源内容、写正式电影包或 MOVE。`.env.p2` 仅供验证工具，不会自动导入产品配置，使用应用需在设置页单独保存。
- 真实电影包完整闭环、P2 新产物 Kodi 导入 / 刷新 / 真正断网、飞牛实机、真实大库和外部并发仍未验证。P0 未验项保持原状态。
- 事件仍为 REST 游标轮询；电视剧与完整批量 / SSE UI 留待 P4，字幕 / manifest / MOVE 留待 P3，正式 NAS 发布留待 P5。
- 前端 Ant Design 主块约 1.09 MB / gzip 348 KB，有体积提示；Starlette 测试客户端有 HTTPX 弃用提示，当前锁文件构建 / 测试通过。

## 后续阶段交接格式

每次实施阶段后更新上面的状态，并追加：实现内容、相关提交 / 分支、实际运行的命令和结果、Mock 与真实验证边界、剩余错误 / 未验证事项、下一步入口。不能只凭代码生成或 Mock 通过标记实机验收完成。

## P3 实施记录：2026-10-09

沿用分支 `codex/p1-foundation` 与草稿 [PR #1](https://github.com/gteall/reeldock/pull/1)。新增 `completion.py`、`probe.py`、`subtitles.py`、射手 Provider、Alembic `0003`、P3 API / UI 与验证工具；保留 `movie_base` 的 P2 双阶段边界。新增依赖 pysubs2 / charset-normalizer / pycountry / OpenCC，开发镜像包含 FFmpeg。

整包基础门禁在队列及字幕 handler 双重验证，并在首次探测前再次完整读回基础小资产。中文分支不创建探测会话、不读视频 / 现有字幕、不实例化射手 Provider；未知原始语言或默认音轨歧义待确认。非中文分支保存实际默认音轨选择依据，内置简体文本须解析与完整性粗校验；已有匹配外挂任意语言均可满足，IDX/SUB 须成对验证。下载编码 / 延时 / 时间轴及上传 SHA-256 验证均独立，失败不归档。

最终 manifest 按版本与内容寻址，保留旧清单；普通附件及控制目录中的用户附件全部纳入目录快照。MOVE 意图先提交，随后 Overwrite:F，再核对两侧、清单身份、视频版本与必需小资产完整哈希。207 子项失败保存相对路径 / 数量。未知结果不重发、不删除；源完整且前次明确拒绝才可重试。扫描 / 新任务不能破坏待恢复意图；远端移动完成但 DB / 检查点提交中断可幂等修复。

已执行：

- Ruff 格式 / 静态检查、前端 4 项测试与 TypeScript / Vite 构建通过。
- `uv sync --frozen` 与最终 `uv build --wheel` 通过，锁定字幕解析 / 语言 / OpenCC 依赖；Alembic `0003` 包含在 wheel 内。
- `uv run pytest -q`：**181 项通过**。`scripts/p3_verify.py mock`：**80 项 P3 检查通过**，包括基础失败与中文零调用、默认音轨、内置 / 外挂字幕、射手协议 / 延时、坏字幕上传、manifest 失败、目标冲突、207、超时、进程取消、数据库 / 最终检查点失败恢复、源变化及附件核对。MOVE 后加入只读核对的有界退避，不会重发 MOVE；有效外挂优先复用，新增回归证明不会进行不必要的内置字幕抽取。
- 本机真实 FFprobe / FFmpeg 通过回环 Range 网关处理自建两秒 MKV；预算、Range 拒绝、超时杀进程 / 回收及输出限额测试通过。没有读取用户视频。
- 真实射手公开哈希返回 3 个 ASS 候选；负延时 -20 秒裁去片头 8 个 cue 后，453 个有效 cue 解析通过。只证明公开样本查询 / 下载 / 格式，不证明真实电影匹配或同步。报告 `reports/p3-shooter.json` 本地忽略。
- 真实 WebDAV 首轮：自建中文分类小样本基础资产及 manifest 上传通过；MOVE 后源消失 / 目标存在、视频版本不变，4 个小资产全文哈希一致，但部分 NFO / JPG 的 ETag / mtime 改变，安全停为 move_unknown。经只读诊断，核对器修正为仅对全文哈希一致的小资产允许元信息变化，视频 / 普通附件仍严格比较；首轮报告保留，最终复验单列。
- 本机浏览器隔离 Mock：配置范围确认、两次扫描稳定、中文任务五步完成 / 路径更新 / 字幕跳过 / 实际音轨未探测 / manifest 预览通过；英语任务完成补充字幕及归档，已归档包的启动与改 ID 按钮禁用。截图 `reports/p3-ui.png`，不代表 Kodi 或 NAS 生产验收。
- 开发容器构建通过；最终包含 OpenCC 与 FFmpeg 的非 root 镜像通过 **9 项**独立容器 / 卷重启烟测，以及容器内 **4 项**真实 FFprobe / FFmpeg 网关测试。无真实片源。
- P0 `python -m unittest discover -v` **44 项通过**，包含专用媒体能力矩阵；这不改变正式流程的字幕后置规则。

边界：真实大电影远程 seek / 整条字幕归档链路、IDX/SUB 真实样本、真实 NAS 207 / 超时故障、Kodi 真正断网、飞牛发布仍未验证。当前简体及完整性是明确标签 + 有界文本 / OpenCC 字形与时间轴证据，不保证每句同步；无 OCR、无自动压缩包解包、无跨驱动复制删除。精确版本 / 驱动未知时不能把测试根目录能力推广到生产路径。

P3 最终真实 WebDAV 复验：`scripts/p3_verify.py webdav --output reports/p3-webdav-final.json` **passed**；新随机专用目录的五步流程完成，字幕 skipped_tmdb_chinese、archive / intent 均 archived、零视频内容读取 / 探测 / 射手。报告与持久化验证日志保存在本地忽略目录，远端两组测试包保留未删除；未更改产品配置或为用户生产路径启用 MOVE。

最终 wheel / 静态资源 / 开发镜像已构建；临时界面服务已停止。提交前差异、格式及敏感内容检查通过，环境文件、报告、下载字幕、测试素材及数据库不纳入提交。下一阶段入口为 P4 提示词。


## P4 实施记录：2026-10-09

分支继续使用 `codex/p1-foundation`，沿用草稿 [PR #1](https://github.com/gteall/reeldock/pull/1)。新增剧集解析、剧 / 季 / 集元数据计划、Alembic `0004`、逐集检查点及资产关联、SSE 服务和浏览器重连；既有电影处理、WebDAV、缓存、上传校验、字幕规则、清单和 MOVE 意图恢复共同复用。没有第二套电视剧字幕 / 归档实现。依赖沿用 uv / pnpm 锁文件，没有新增运行库。

已实现：

- 按剧根归包，支持 SxxEyy、NxM、中文季集、Specials；未知、重复、冲突和多集单文件待确认。人工映射带版本 / 租约 / 意图保护，重新扫描稳定后生效。旧版季目录包可安全重组；活跃 / 未决归档不能被扫描改写。
- TMDB 剧、季、集详情和图片缓存，字段级中文回退；所有集继承剧级 original_language（包括缺失），单集接口中文字段不能改变分类。只要求当前已下载文件，不等待未来集数。
- Kodi tvshow.nfo、逐集 NFO、共享 poster / fanart / .actors，季根海报、视频旁缩略图独立管理。现存 NFO 的人工文字 / 观看进度不覆盖；ID / 季集冲突阻塞。基础 NFO 不依赖探测。
- 冻结整包必需基础集合，全部读回验证才逐集字幕；中文所有集零探测 / 视频读取 / 射手。非中文逐集使用既有默认音轨 / 内置 / 外挂 / 射手规则；任一集失败保留整包，重试核对并复用成功集与基础资产。
- 最终 manifest 纳入已验证增强图哈希，移动后增强图 ETag/mtime 变化须全文哈希一致才能接受。早期清单未记录增强图时，仅使用与意图源快照、当前上下文完全绑定且早于意图的 DB 读回证据，随后完整读取目标图核对；视频和普通附件的严格版本规则保持。坏内容不因元信息放宽而通过。
- 目录树 / 展开季集表、逐集证据、NFO / 图片 / 字幕预览、演员完成数、人工匹配、失败 / 缺字幕筛选、批量启动 / 暂停 / 继续 / 重试和逐项拒绝反馈。资产局部任务使用同一包租约，归档后可补增强图而不重做字幕 / MOVE。
- 持久化 SSE ID 重放 / Last-Event-ID / reset / 会话到期，浏览器退避重连和 REST 状态刷新，30 秒轮询兜底；退出与卸载取消流。DB 事务不跨网络 yield / sleep。

本次验证记录：

- Ruff 静态 / 格式、锁文件冻结同步、后端 wheel（含 `0004`）、前端 TypeScript / Vite 构建通过；Vite 对既有 Ant Design 公共块给出体积提示，构建成功。空隔离数据库 0004 → 0003 → 0004 迁移往返通过，升级保留已有配置 / 任务的回归通过；受保护的 P3 MOVE 意图保留旧快照格式，可正常完成而不被 P4 kind 字段误判失效。
- P4 自动化覆盖中文 / 未知语言零调用、所有基础读回先于首次探测、共享图片 / 头像 / 单集 NFO 失败、逐集实际音轨、字幕失败与补齐复用、元数据局部恢复、Specials、源增加一集、目标冲突、已有 NFO / 观看进度保护、增强项独立重试、MOVE 崩溃恢复及增强图元信息变化 / 内容损坏。
- API 验证人工修正、陈旧版本 / 重复映射 / 多集合并拒绝、CSRF、批量幂等与暂停 / 继续、预览认证、旧季包重组。真实本机 Uvicorn + HTTPX / SQLite / Worker + SSE 端到端：连接关闭期间任务完成，Last-Event-ID 重连重放，并通过 REST 获取完成后的剧包；Provider 为内存 Mock，零用户视频。
- 浏览器隔离 Mock：登录、两次扫描稳定、三个包批量启动；中文电影 / 中文剧完成，英语剧第 2 集无字幕时整包阻塞，第 1 集成功状态保留；补入原创英文外挂后“重试失败集”完成整包归档。逐集字幕 / NFO 预览、剧季集树、原始语言和状态可见，截图 `reports/p4-ui.jpg` 本地忽略。
- `scripts/p4_verify.py tmdb --output reports/p4-tmdb.json`：真实 TMDB 剧 1399、第 1 季（10 集）、第 1 集通过；剧 / 集原始语言均为 en，poster 85,515 B、fanart 172,852 B、季海报 87,733 B、thumb 10,584 B 实际下载解码。只读 API 分项，不访问用户媒体。
- `scripts/p4_verify.py webdav --output reports/p4-webdav.json`：真实专用随机目录的两集自建字节包，Mock 中文 TMDB，全部基础 / 增强资产上传、读回和 manifest 通过，中文跳过、零视频内容读取。首轮 MOVE 后源消失 / 目标存在、目录集合相同；必需项及增强图全文哈希一致，但增强图 ETag / mtime 变化，旧清单未包含增强图哈希，安全停为 move_unknown。首轮报告保留，没有重发 MOVE、没有 DELETE。

真实剧集素材字幕链路、大媒体远程 seek / 预算、剧集 Kodi 新产物导入 / 刷新 / 真正断网、飞牛部署、真实 NAS 故障注入仍未验证。常规季集之外的绝对集数 / 替代组、多集单文件、目标目录自动合并、TV IMDb 自动查找尚不支持。增强图失败独立显示，不能当作成功；普通未验证附件若移动后版本改变仍会安全停下。

P4 真实专用 WebDAV 恢复：`scripts/p4_verify.py reconcile --journal reports/p4-runs/.reeldock-p4-<实际ID> --output reports/p4-webdav-recovered.json` **passed**，archive / intent 均为 archived。复用原意图，完整核对目标必需项和增强图；工具禁止远程 PUT / MKCOL / MOVE / Range，恢复期间远程写入 **0**、视频读取 **0**。首轮失败与只读恢复报告分别保留，没有改写历史 manifest，没有操作用户实际片源。

最终检查：`uv run pytest -q` **226 项通过**；`scripts/p4_verify.py mock` **45 项 P4 用例通过**；P0 `unittest discover -v` **44 项通过**；前端 **8 项测试通过**，格式 / TypeScript / Vite 构建通过。最终 wheel 与开发镜像构建通过，容器内 P4 + 真实自建 FFprobe 网关 **49 项通过**；独立容器 / 卷重启烟测 **9 项通过**。敏感内容核对区分通用 admin / 公开射手接口 / 回环代理示例，实际凭证、NAS URL 和媒体 / 测试路径零匹配；env、reports、素材、字幕、DB 不纳入提交。没有正式发布、没有飞牛生产部署，下一入口为 P5 提示词。

### P4 使用修正：OpenList / 115 的 302 直链（2026-10-09）

真实使用中，默认空主机白名单阻止了 OpenList 的跨域资产读回。产品现将 `redirect_hosts` 作为可选限制：留空自动跟随管理员配置的 WebDAV 返回的 GET / HEAD 下载位置；非空才限制精确主机名。无需维护 115 下载域名，不改 OpenList 302 模式，不切换中转。已有空列表配置立即适用，配置字段和数据库无需迁移；需重启本机后端加载新代码。

跨域剥离账号密码、Cookie、Referer / Origin 及 DAV 条件 / 锁头；链路返回原服务也不恢复凭证。HTTPS 降级、含账号密码 / 非 HTTP(S) URL、过多跳转、DAV 写入跳转仍拒绝。Range 的 206 / Content-Range、字节预算及整包基础门禁保持。界面将主机限制标为可选，并将 HTTP 405 改为“请求方法被拒绝”，不再一概声称 WebDAV 端点填错。

新增 **14 项**下载链路回归：动态多主机、凭证 / Cookie / 条件头剥离、返回源站不恢复凭证、手动限制、非法跳转、跳转上限、禁止 DAV 方法跳转、Range 返回 200 不消费视频正文。`pytest -q` **240 项通过**；Ruff 静态 / 格式通过；前端 **8 项测试**、格式、TypeScript / Vite 构建通过。旧 P0 验证工具仍保留独立的精确白名单规则。

只读真实复查：使用产品已有配置（主机列表为空）读取一个之前由影坞生成的 NFO，实际 `302 → 200`，**17,756 B**，与已记录 SHA-256 一致；客户端观察到跨域请求无 Authorization / Cookie。报告 `reports/p4-redirect-readback.json` 本地忽略；远程写入 **0**、视频读取 **0**。仅证明这次小资产读回，不代表完整媒体字幕 / 归档或全部 405 / 超时原因已解决。未更改用户配置、未重启正在运行的用户应用、未更改 OpenList 设置。

## P5 实施记录：2026-10-17

沿用 `codex/p1-foundation`，复用 P1–P4 的 Provider、队列、字幕/归档组件，未复制另一条流程。`Dockerfile` 分阶段构建前端和冻结 Python 依赖，运行层只含应用、静态资源与 FFprobe/FFmpeg。`compose.yaml` 默认非 root 10001，可指定 UID/GID；只读根、独立 /data 与 /cache、tmpfs、健康检查、10 MiB × 3 日志轮转及 60 秒退出窗口。/data 必须位于 NAS 本地磁盘。

SIGTERM 到达即停止领取新任务，现有 HTTP/SSE 最多等 20 秒，再结束 Worker、释放租约。单实例文件锁拒绝同目录多进程；启动/迁移在任何 DDL 前检查 SQLite、已知 schema 和配置/密钥配对。新维护命令 `backup` 使用 SQLite online backup API，归档数据库+密钥+SHA-256 清单（0600，不覆盖旧备份）；`restore` 仅向空目录写入，拒绝损坏/未知 schema/缺失密钥/穿越/重复成员，缓存独立可重建。

新增 `scripts/p5_container_smoke.py`、`p5_fixture.py` 和 `p5_nas.py`：仅使用独立容器、卷/专用测试父目录子目录；真实业务组件 + 显式 Mock 外部服务 + 实际 FFprobe，单独记录真实 NAS Compose 与只读 OpenList 检查。所有凭证走本地忽略文件/stdin，不改变用户运行中的应用、OpenList 302 模式或 Docker 权限。

关于页使用官方批准、原样打包的 TMDB SVG；浏览器已验证来源/声明和开发版本状态，截图 `reports/p5-about.png`。部署、网络、代理、备份、回退与 v0.1.0 草案已补齐。CI 的生产构建工作流 `push:false`；版本发布仅手动且需已存在的匹配版本标签，本阶段未运行。

已执行自动化：后端 `pytest -q` **255 项通过**（新增 P5 15 项）；P0 回归 **44 项通过**；前端 **8 项测试**、Prettier、TypeScript/Vite 构建通过；Ruff 静态/格式、wheel、差异检查通过。新增测试覆盖 WAL 备份、配置/管理员/任务/密钥恢复、无缓存恢复、损坏备份/错误密钥/未知 schema、活动实例拒绝、升级失败后恢复旧 schema、SIGTERM 停止领取和安全重排队。P0 首轮受沙箱回环限制失败，提升本机测试权限后完整通过；这不是外部服务结果。

NAS 预检：用户指定专用环境 SSH/sudo 通过，x86_64，Docker **28.5.2**，Compose **2.40.3**，测试父目录存在可写；观察到 OpenList 容器镜像标签 `v2.8.2`，**不能当作软件精确版本或驱动名称**。

Kodi P5 独立验收仍 **未验证**：进程级断网沙箱确实拒绝外网连接（errno 1），但临时复制的 portable Kodi 签名校验失败，并在出现 UI 前退出 137；没有在这个实例完成首次导入/刷新。保留 `reports/p5-kodi.json`，未绕过系统签名检查，不把 P0 在线电影导入/刷新当本次剧集/断网通过。已有用户 Kodi 资料库未加入本次 P5 测试源。

生产镜像实际构建通过（官方基础镜像公共 ECR 来源）；最终运行镜像 ID 为 `sha256:d2b4ff21536a071975001e9d212d022b3f2e91f6811c0dca7962766fbbd098cd`，Python **3.12.15**、FFprobe/FFmpeg **5.1.9-0+deb12u1**。本机 Docker 的 amd64 镜像以仿真运行，`p5_container_smoke.py` **17 项检查通过**；完整报告 `reports/p5-container.json`，生成合成 Kodi 样例在报告同级随机目录。

首轮验证失败如实保留：空命名卷挂在不同路径初始化后，Docker copy-up 重置属主，健康超时；工具改用 `nocopy`。随后第 7 项之后的退出码断言失败，确认锁定 Uvicorn 会在优雅关闭后重抛 SIGTERM，改为接受 0/143 并核对持久化状态，实际为 143。NAS 首轮工具还漏挂独立缓存，已将专用真实存储工具缓存放到其本地测试目录；未通过的首轮结果保留，不把镜像构建当成容器通过。最终 17 项为修正后真实重跑结果，生产字幕/MOVE 代码没有为通过测试而替换。

NAS 实机分项已通过：仓库 Compose 在专用本地 bind 目录中以 UID/GID **12001** 健康启动；同一候选镜像的完整 **17 项**容器矩阵在原生 amd64 重跑通过。本地文件系统 stat 输出 `ext2/ext3`（ext 系列），未将 SQLite 放到 WebDAV。NAS 模拟远端故障矩阵与真实存储检查分开记录。

真实网络分项：NAS 生产镜像实际列出 OpenList 目录；实际 TMDB 电影 550 详情/海报（**120,626 B**，original_language=en）通过；射手公开哈希返回 **3** 候选并下载/验证 **56,379 B** 字幕。均无真实视频读取，没有以公开字幕样本宣称实际影片同步通过。当前 NAS 测试没有配置应用代理，不复用开发机回环代理。

真实 WebDAV 最终结果：在原有专用 NAS 子目录修正工具权限后，复用同一候选镜像重跑。`scripts/p5_nas.py` 的公开安装/Compose/真实存储函数及 `p5_container_smoke.py` 由本地临时重试入口 `/tmp/reeldock-p5-nas-retry.py` 调用；未重新复制一套业务逻辑。最终 NAS 报告 status=passed 仅指其明示范围，`p5_full_acceptance=unverified` 保持。

电影与双集剧均通过：Mock TMDB original_language=zh、自建媒体字节、真实 OpenList 资产 PUT/全量读回、中文跳过、最终清单、真实 MOVE 和目标核对，task=completed，intent/archive=archived；零视频读取。MOVE 调用 **电影 1 / 剧包 1**，剧包服务器完成后注入 **1 次客户端 storage_timeout**，核对后完成，未重发 MOVE/DELETE。没有将客户端注入称为真实 NAS 网络故障，也没有用合成包证明真实影片匹配或 Kodi 可播放性。测试目录保留。

第二轮还发现 NAS 传输的中间源码目录权限为 000、专用 bind 目录需显式 chmod；工具现明确设置仅含公开源码的目录 0755/文件 0644（外层仍 0700）、应用数据/缓存 0700 并赋予运行 UID。最终 NAS Compose 和 17 项均在修正后实际重跑；先前失败报告/私有目录日志均保留。

交付入口：[部署](deployment.md)、[备份/升级/回退](upgrade.md)、[P5 复验](p5-tools.md)、[v0.1.0 草案](releases/v0.1.0.md)、CHANGELOG、TMDB 来源说明。完整真实影片/剧集字幕命中与同步、大媒体、Kodi 无缓存断网、NAS 断电/磁盘故障/旧镜像回退、反向代理 SSE 和 arm64 仍未验证。测试容器/卷已清理，专用测试目录与候选镜像保留，没有启用生产媒体归档或发布正式版本。
