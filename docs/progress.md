# ReelDock · 影坞：开发进度与交接

更新日期：2026-10-09。

## 当前状态

P1 工程基础已实施：前后端、本地持久化配置 / 任务 / 事件、包级租约、检查点、登录、只读连接检查和开发容器可运行，已验证重启恢复。完整刮削与归档留待 P2 / P3，当前不移动实际媒体。P0 已完成部分真实 WebDAV / 射手 / Kodi 联调，**完整 P0 仍未验收完成**：Kodi 真正断网、大型实际片源、真实 NAS 故障注入及版本信息待补齐。按用户本次明确要求推进 P1，不把 P0 待验项标记为完成。

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
- 通过 P1 后端 61 项、前端 4 项、P0 回归 44 项测试；完成后端 wheel、前端静态资源和本机开发镜像构建，容器重启持久化烟测通过。

## 阶段状态

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| P0 外部能力验证 | 工具完成，部分实机验收待补 | 离线回归及真实 WebDAV / 射手 / Kodi 导入刷新已通过；断网、大型片源和真实故障待验 |
| P1 工程基础 | 已完成本阶段验收 | 本机 / 开发容器启动、配置与任务持久化、竞争领取 / 恢复测试通过；实际刮削及 MOVE 关闭 |
| P2 电影基础刮削 | 未开始 | 尚无正式 TMDB / WebDAV 刮削闭环 |
| P3 字幕与归档 | 未开始 | 规则已设计，尚无实现 |
| P4 电视剧与完整 UI | 未开始 | 尚无实现 |
| P5 NAS 发布 | 未开始 | 已有 P1 开发镜像；尚无正式镜像发布 / 飞牛实机验收 |

## 最新业务约束

- 整包基础 NFO / 图片 / 演员必需资产先全部远程验证，才能进入字幕阶段。
- TMDB original_language 默认中文集合 zh / cn，命中后 skipped_tmdb_chinese，零视频探测、零射手哈希和请求。
- TMDB 非中文才探测实际音轨 / 字幕；已有有效外挂不限语言。
- 基础 NFO 无 FFprobe 依赖，未探测 streamdetails 省略。
- 最终 manifest 和安全移动仍需通过，不因中文跳过字幕而跳过归档校验。

## 下一步

进入 P2：实现电影扫描 / 稳定窗口、TMDB MetadataProvider、基础 NFO / 图片 / .actors 上传读回验证及资产状态页面，继续保持媒体探测、射手及归档关闭。真实 TMDB 联调需要用户在设置页保存凭证；P1 仅保存凭证，不请求 TMDB。开发启动与扩展入口见 [development.md](development.md)。

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

分支：`codex/p1-foundation`。保留 P0 工具，新增 `backend/src/reeldock`、`backend/tests`、`frontend/src`、锁文件、`Dockerfile.dev` / `compose.dev.yaml` / `.devcontainer`、P1 CI，以及 [开发指南](development.md)。

数据库首次迁移 `0001` 覆盖配置、管理员 / 会话 / 登录限流、包、媒体、资产、任务、步骤、事件和归档意图。数据库放本地磁盘，配置经 Fernet 加密，密码用 Argon2id 哈希；管理员通过本机 CLI 初始化。修改接口具有 Origin / CSRF 校验，日志与事件只保留安全代码。当前事件接口为持久化 REST 游标，SSE 入口语义已预留但传输尚未开启。

真实执行的命令 / 验证：

| 命令 / 操作 | 结果与证据范围 |
| --- | --- |
| `uv sync --frozen`、`uv build --wheel` | 后端锁定依赖与 wheel 构建通过，迁移脚本 / 模板包含在 wheel 内 |
| `uv run ruff check backend scripts/p1_*.py`、`uv run ruff format --check backend scripts/p1_*.py` | 通过 |
| `uv run pytest -q` | 61 项通过；配置加密 / 重启、无效路径、来源与 CSRF、幂等并发提交、竞争领取、租约过期和旧持有者拒绝、心跳、暂停、局部重试、阶段顺序及上下文失效；含真实子进程非正常退出恢复 |
| `pnpm install --frozen-lockfile`、`pnpm format:check`、`pnpm test`、`pnpm build`（frontend） | 格式、4 项 API 传输测试、TypeScript 与 Vite 构建通过 |
| `python3.12 -m unittest discover -v` | P0 的 44 项回归通过，保留 P0 的专用媒体能力测试与正式流程的区别 |
| `uv run python scripts/p1_check_webdav.py --env-file .env.p0` | 新 HTTPX Provider 在真实 WebDAV 列出扫描目录 2 项、测试父目录 3 项；零视频内容读取、零写入、零 MOVE |
| 本机浏览器实际操作 | 管理员登录、无效输入 / 输出路径被拒绝、正确配置保存与刷新保留、Mock WebDAV 经 Worker 完成任务及检查点展示；截图在忽略的 `reports/p1-ui.jpg` |
| `docker compose -f compose.dev.yaml build` | Docker Hub 鉴权端点最初超时；使用 `NODE_IMAGE` / `PYTHON_IMAGE` 的公共 ECR 镜像来源参数构建通过，未修改全局 Docker 配置 |
| `docker compose -f compose.dev.yaml up -d --no-build` | 本机容器健康检查和静态前端通过，单 Uvicorn 进程，非 root 用户 |
| `uv run python scripts/p1_container_smoke.py` | 独立随机容器 / 数据卷 9 项检查通过；真实重启保留配置、会话、暂停任务和事件；重复提交幂等；仅测试数据，无真实 WebDAV |
| `git diff --check` | 通过；本地凭证、报告、数据库、样例和构建产物不提交 |

任务边界：`match_metadata → base_assets_verified → subtitle_policy → final_manifest → archive`。前驱检查点和当前版本远程资产证据都要通过，空集合不能满足基础门禁。失败只重试当前步骤，已完成基础步骤在字幕重试中保留；源快照 / TMDB / 语言 / 策略变化使旧证据失效。P1 没有生产 TMDB、媒体探测或射手 handler；archive 的 Worker 门禁和 WebDAV MOVE 都关闭，PUT 同样尚未开放。只有真实只读连接任务可以执行完成。

剩余边界：真实代理、飞牛 Docker 与容器访问真实 NAS 未验证；VS Code 交互式 Reopen in Container 未实测。P0 的断网 Kodi、大媒体及 NAS 故障待验项保持不变。当前 Ant Design 主块约 1.05 MB / gzip 336 KB，构建体积提示留待后续 UI 拆分；Starlette 对测试客户端 HTTPX 的弃用提示不影响当前锁定依赖结果。没有发布版本或上传镜像。

下一阶段从 P2 提示词开始，复用 Provider、资产版本与 Worker 检查点；不要启用任何提前探测路径。新配置页面可先保存 TMDB 凭证，P2 再进行真实接口验证。

## 后续阶段交接格式

每次实施阶段后更新上面的状态，并追加：实现内容、相关提交 / 分支、实际运行的命令和结果、Mock 与真实验证边界、剩余错误 / 未验证事项、下一步入口。不能只凭代码生成或 Mock 通过标记实机验收完成。
