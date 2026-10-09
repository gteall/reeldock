# ReelDock · 影坞：开发进度与交接

更新日期：2026-10-09。

## 当前状态

P3 字幕与安全归档已实施：基础必需资产全部上传并读回后才进入字幕策略；中文 zh/cn 零探测 / 视频读取 / 射手调用，明确非中文按实际默认音轨与字幕证据处理。已接入受限 Range 网关、射手下载验证、不可变 manifest、Overwrite:F 与持久化 MOVE 核对恢复。自动归档默认关闭，需管理员确认当前存储范围能力。Mock / 本机自建媒体测试通过；真实验证与未验证边界见下方 P3 记录。P0 真正断网 Kodi、大型片源、飞牛实机及驱动精确版本仍未验证。

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
- 完成 P3 字幕策略、射手 Provider、不可变最终清单和安全归档恢复；后端 180 项测试通过，真实专用 WebDAV 中文小包归档及公开射手样本分项通过。

## 阶段状态

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| P0 外部能力验证 | 工具完成，部分实机验收待补 | 离线回归及真实 WebDAV / 射手 / Kodi 导入刷新已通过；断网、大型片源和真实故障待验 |
| P1 工程基础 | 已完成本阶段验收 | 本机 / 开发容器启动、配置与任务持久化、竞争领取 / 恢复测试通过；实际刮削及 MOVE 关闭 |
| P2 电影基础刮削 | 已完成实现与 Mock 验收，真实分项通过 | 真实 TMDB、WebDAV 资产上传通过；真实电影包、P2 Kodi / 飞牛未验证 |
| P3 字幕与归档 | 已实现，Mock / 本机媒体验收通过 | 真实射手分项通过；专用 WebDAV 验证记录见下文，生产路径 / 大媒体 / 真正断网 Kodi 未验证 |
| P4 电视剧与完整 UI | 未开始 | 尚无实现 |
| P5 NAS 发布 | 未开始 | 已有 P1 开发镜像；尚无正式镜像发布 / 飞牛实机验收 |

## 最新业务约束

- 整包基础 NFO / 图片 / 演员必需资产先全部远程验证，才能进入字幕阶段。
- TMDB original_language 默认中文集合 zh / cn，命中后 skipped_tmdb_chinese，零视频探测、零射手哈希和请求。
- TMDB 非中文才探测实际音轨 / 字幕；已有有效外挂不限语言。
- 基础 NFO 无 FFprobe 依赖，未探测 streamdetails 省略。
- 最终 manifest 和安全移动仍需通过，不因中文跳过字幕而跳过归档校验。

## 下一步

P3 使用与复验见 [p3-tools.md](p3-tools.md)，启动见 [development.md](development.md)。下一阶段按 P4 提示词接入电视剧与完整批量 / SSE UI，保留整包基础门禁、original_language 语义及归档核对。当前 OpenList 忽略 PUT 条件头，客户端 stat 不能当作原子 CAS，包须独占管理。

P0 待验仍需补充精确 OpenList 版本 / 存储驱动；在有隔离条件时验证 Kodi 真正断网且无缓存的新 ID。大型 MKV / MP4 与本地副本、真实故障注入均留作明确未验证。飞牛 NAS 正式部署和发布在 P5。

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
- `uv run pytest -q`：**180 项通过**。`scripts/p3_verify.py mock`：**79 项 P3 检查通过**，包括基础失败与中文零调用、默认音轨、内置 / 外挂字幕、射手协议 / 延时、坏字幕上传、manifest 失败、目标冲突、207、超时、进程取消、数据库 / 最终检查点失败恢复、源变化及附件核对。最后加入 MOVE 后只读核对的有界退避，相关完成流程 **57 项回归通过**，不会重发 MOVE。
- 本机真实 FFprobe / FFmpeg 通过回环 Range 网关处理自建两秒 MKV；预算、Range 拒绝、超时杀进程 / 回收及输出限额测试通过。没有读取用户视频。
- 真实射手公开哈希返回 3 个 ASS 候选；负延时 -20 秒裁去片头 8 个 cue 后，453 个有效 cue 解析通过。只证明公开样本查询 / 下载 / 格式，不证明真实电影匹配或同步。报告 `reports/p3-shooter.json` 本地忽略。
- 真实 WebDAV 首轮：自建中文分类小样本基础资产及 manifest 上传通过；MOVE 后源消失 / 目标存在、视频版本不变，4 个小资产全文哈希一致，但部分 NFO / JPG 的 ETag / mtime 改变，安全停为 move_unknown。经只读诊断，核对器修正为仅对全文哈希一致的小资产允许元信息变化，视频 / 普通附件仍严格比较；首轮报告保留，最终复验单列。
- 本机浏览器隔离 Mock：配置范围确认、两次扫描稳定、中文任务五步完成 / 路径更新 / 字幕跳过 / 实际音轨未探测 / manifest 预览通过；英语任务完成补充字幕及归档，已归档包的启动与改 ID 按钮禁用。截图 `reports/p3-ui.png`，不代表 Kodi 或 NAS 生产验收。
- 开发容器构建通过；最终包含 OpenCC 与 FFmpeg 的非 root 镜像通过 **9 项**独立容器 / 卷重启烟测，以及容器内 **4 项**真实 FFprobe / FFmpeg 网关测试。无真实片源。
- P0 `python -m unittest discover -v` **44 项通过**，包含专用媒体能力矩阵；这不改变正式流程的字幕后置规则。

边界：真实大电影远程 seek / 整条字幕归档链路、IDX/SUB 真实样本、真实 NAS 207 / 超时故障、Kodi 真正断网、飞牛发布仍未验证。当前简体及完整性是明确标签 + 有界文本 / OpenCC 字形与时间轴证据，不保证每句同步；无 OCR、无自动压缩包解包、无跨驱动复制删除。精确版本 / 驱动未知时不能把测试根目录能力推广到生产路径。

P3 最终真实 WebDAV 复验：`scripts/p3_verify.py webdav --output reports/p3-webdav-final.json` **passed**；新随机专用目录的五步流程完成，字幕 skipped_tmdb_chinese、archive / intent 均 archived、零视频内容读取 / 探测 / 射手。报告与持久化验证日志保存在本地忽略目录，远端两组测试包保留未删除；未更改产品配置或为用户生产路径启用 MOVE。

最终 wheel / 静态资源 / 开发镜像已构建；临时界面服务已停止。提交前差异、格式及敏感内容检查通过，环境文件、报告、下载字幕、测试素材及数据库不纳入提交。下一阶段入口为 P4 提示词。
