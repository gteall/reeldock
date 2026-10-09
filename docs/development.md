# 本地开发与交接（P1 / P2）

更新日期：2026-10-09。已实现 P1 工程基础和 P2 电影基础资产闭环；字幕与归档执行从 P3 开始。电影操作、凭证与真实验证范围见 [P2 指南](p2-tools.md)。

## 本机启动

要求 Python 3.12、uv 0.9.10 或兼容版本、Node.js 22.12+、pnpm 10.33.0。依赖由根目录 `uv.lock` 和 `frontend/pnpm-lock.yaml` 固定。

在仓库根目录执行：

```bash
uv sync --frozen
uv run reeldock init-admin
```

初始化会交互式输入至少 12 位管理员密码，只允许创建一次。没有默认密码，也没有公开的初始化 API。管理员使用 Argon2id 密码哈希；不要把密码放进命令行参数。需要脚本初始化时，可通过 `reeldock init-admin --password-stdin` 的标准输入传入。

前端安装与构建：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
uv run uvicorn reeldock.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

访问 `http://127.0.0.1:8000`；健康检查是 `GET /api/health`。一个 Uvicorn 进程在 lifespan 中运行两个有界 Worker 槽位；**不要用多个 Uvicorn workers 或同时启动多个应用实例**。P1 未实现媒体扫描、TMDB 调用或任何媒体移动。

开发时也可在第二个终端运行：

```bash
cd frontend
pnpm dev
```

浏览器访问 `http://localhost:5173`，Vite 将 `/api` 代理给本机 8000 端口。前端使用 Ant Design 和 TanStack Query；配置、任务与事件都来自后端，没有演示状态替代实际 API。

## 配置、密钥与登录

运行时环境变量见 `.env.example` 的 P1 部分，可单独保存到忽略文件 `.env.app`。P0 的 `.env.p0` 不会自动变成应用配置，也不会自动确定正式归档目录。

- `REELDOCK_DATA_DIR` 默认 `data`：本地 SQLite、WAL 和 `master.key`。必须位于本地文件系统，不能放在 WebDAV / 网络数据库目录。
- `REELDOCK_FRONTEND_DIR` 默认 `frontend/dist`。
- `REELDOCK_ALLOWED_ORIGINS` 是 JSON 字符串数组，必须列出实际浏览器来源，包含协议和端口。默认允许本机 8000 / 5173。改变端口、使用 NAS 主机名或反向代理时需显式修改。
- `REELDOCK_SECURE_COOKIE=false` 适合本机 HTTP；HTTPS 访问时改为 `true`。
- `REELDOCK_WORKER_ENABLED`、`REELDOCK_WORKER_CONCURRENCY`、`REELDOCK_LEASE_SECONDS` 控制 Worker。租约默认 30 秒，心跳每租约时长的三分之一续租。

设置页可保存 WebDAV 地址、账号、密码、输入 / 输出目录、TMDB 凭证、代理、精确下载重定向主机白名单和未来探测预算。密码和 TMDB 凭证从不回显，留空保留原值，勾选“清除”才删除。保存带配置版本，旧页面覆盖会收到 409。

路径使用相对于 WebDAV 端点的已解码绝对路径，例如 `/incoming`；WebDAV 地址通常包含 `/dav/`。输入 / 输出不得相同、互为上下级或为根目录；路径中的 `.`、`..`、反斜杠及编码后的穿越会被拒绝。Unicode、空格、`#`、字面量 `%` 支持一次正确编码。

整个连接配置使用 Fernet 加密后存入数据库，密钥文件权限为 0600。已有加密配置却缺失 / 不匹配密钥时启动失败，不自动替换密钥。**备份和恢复必须同时包含数据库与 master.key**；SQLite 正在运行时使用 SQLite backup API，不要单独复制可能尚未 checkpoint 的主文件。P5 再提供正式备份工具和密钥轮换。

登录后使用持久化的、不透明 HttpOnly / SameSite=Strict 会话 cookie，24 小时过期；修改接口还要求允许的 Origin 和会话对应的 `X-CSRF-Token`。登录同样校验来源，并持久化限制同来源 IP 每分钟 5 次尝试。通用 HTTP 客户端可先读取登录响应或 `/api/auth/session` 获取 CSRF；不提供 CORS 跨站访问。生产部署的 TLS 和反向代理细节留待 P5。

日志仅输出白名单事件代码及等级；不输出请求 URL、正文、原始异常或 SQL 参数。HTTPX、Uvicorn 日志统一进入此格式，启动时较早的进程提示不包含连接配置。API 校验错误移除原始 input / context，避免把密码从错误响应带回。任务事件只保存安全代码、阶段及计数，不保存目录列表、签名 URL 或第三方响应正文。

## 连接检查和本地 Mock

“检查已保存连接”创建持久化任务，通过 StorageProvider 对输入和输出目录分别执行 `PROPFIND Depth:1`。返回数量和只读模式；不读取视频、不上传文件、不验证 MOVE 能力。认证失败、路径不存在、权限错误、超时分别记录错误代码；可重试的错误最多自动尝试 3 次，退避为 2、4 秒。

没有真实环境也能启动本机生成式 Mock：

```bash
uv run python scripts/p1_mock_webdav.py
```

使用它打印的回环地址，设置输入 `/incoming`、输出 `/library`，无需账号密码。服务复用 P0 故障服务器，内容只在内存中；测试完成按 Ctrl-C 退出。不能把 Mock 通过记为 OpenList 通过。

已有 P0 凭证时，直接验证新的生产 HTTPX Provider：

```bash
uv run python scripts/p1_check_webdav.py --env-file .env.p0
```

此工具只列出 P0 扫描目录及专用测试父目录，输出安全 JSON 计数，不把 P0 配置复制进应用数据库。退出码 0 通过、1 失败、2 未验证。P1 实测这两个目录均通过，媒体内容读取、写入和 MOVE 均为零。

HTTPX 不隐式使用系统代理，只使用保存的代理配置。PROPFIND 不跟随重定向；GET / HEAD 最多跟随 5 次，跨源要求精确主机白名单，拒绝 HTTPS 降级，移除 Authorization、Cookie 和 DAV 条件头。可选属性 404 允许，资源 / 权限失败不隐藏。Range 必须是精确的 206 / Content-Range；忽略 Range 的 200 在读取响应体前拒绝。Range 方法暂未连接到任何产品媒体操作。

## 开发容器

```bash
docker compose -f compose.dev.yaml up -d --build
docker compose -f compose.dev.yaml exec app /app/.venv/bin/reeldock init-admin
```

访问 `http://localhost:8000`。开发镜像由 Node 前端构建和 Python 后端组成，单应用容器、单 Uvicorn 进程、非 root 用户、带健康检查。命名卷 `reeldock_dev_data` 持久化 `/data`，普通 `down` 保留数据。**不要使用 `down -v` 清理已配置的数据卷**。P1 不发布镜像；飞牛 NAS / GHCR 正式交付属于 P5。

本机 Docker Hub 鉴权地址曾连接超时，已用相同官方基础镜像的公共 ECR 镜像来源完成构建：

```bash
docker compose -f compose.dev.yaml build \
  --build-arg NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim \
  --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm
docker compose -f compose.dev.yaml up -d --no-build
```

自定义 `REELDOCK_DEV_PORT` 时也要修改 `REELDOCK_ALLOWED_ORIGINS`。VS Code 可选择“Reopen in Container”；`.devcontainer/` 提供 Python / Node / uv / pnpm，独立依赖卷避免复用宿主机的 macOS `.venv` / node_modules。开发镜像构建与运行已测；VS Code 的交互式重开流程未实测。

可重复执行的容器验收：

```bash
uv run python scripts/p1_container_smoke.py
```

默认使用本地 `reeldock-app` 镜像与回环 18001 端口，创建随机独立容器 / 数据卷、随机测试密码，检查登录、配置、幂等提交、暂停、重启后配置 / 会话 / 任务 / 事件保留及阶段边界。结束只清理本次创建的容器和卷，不使用真实 WebDAV、不操作任何媒体。

## 任务与扩展契约

REST 接口均位于 `/api`。除健康检查、初始化状态和登录外需要管理员会话：

| 接口 | 用途 |
| --- | --- |
| `GET/PUT /config` | 脱敏读取 / 乐观版本保存配置 |
| `POST /connection-check` | 使用 `idempotency_key` 提交只读连接任务 |
| `POST /tasks` | 提交 movie_base 任务；只处理已扫描稳定的电影包，基础验证后结束 |
| `POST /scan` | 持久化目录扫描任务；使用 idempotency_key |
| `GET /movies`、`GET /movies/{id}` | 发现、候选、原始语言及逐项资产状态 |
| `PUT /movies/{id}/match` | 人工指定电影 TMDB ID；保留现存 NFO 保护 |
| `GET /assets/{id}/preview` | 登录后读取本地 NFO / 图片缓存 |
| `GET /tasks`、`GET /tasks/{id}` | 当前状态及逐阶段检查点 |
| `POST /tasks/{id}/pause|resume|retry` | 暂停、继续、局部重试 |
| `GET /events?after=cursor&limit=100` | 持久化有序事件；保存 next_cursor，断线后继续读取 |

P4 已启用 `GET /events/stream` SSE，支持 Last-Event-ID / after 重连重放；浏览器重连后重新拉取数据库状态，30 秒轮询兜底。配置和事件接口均禁止缓存。反向代理需关闭 SSE 缓冲。

P4 新增 `/packages`（保留 `/movies` 兼容入口）、`PUT /packages/{id}/episodes/{media_id}`、`POST /assets/{id}/retry`、`POST /batch/tasks`、`POST /batch/control`。完整任务 kind=package_pipeline，基础任务 movie_base 也可处理剧包但止于基础资产。输入范围、稳定性、版本、租约及归档意图保护仍适用。剧集布局、人工编号与验证命令见 [P4 指南](p4-tools.md)。

SQLite 开启 WAL、外键和 busy_timeout。短 `BEGIN IMMEDIATE` 事务将任务领取、包租约、状态和事件一起提交；外部 I/O 不放进数据库事务。所有数据库事务目前序列化，适合单实例 P1，后续按实测负载优化只读事务。

相同幂等键和负载返回原任务，变更负载复用相同键返回冲突。不同幂等键可以排队，但同一包只有一个租约持有者。租约含递增代次，旧 Worker 不能续租或提交结果；续租失败只取消当前任务，调度槽位继续领取后续任务。正常停止释放运行任务，非正常退出则等待租约到期后重新领取；不因应用刚重启就偷取仍有效的租约。暂停在当前步骤边界生效，不提前释放正在执行步骤的租约。失败只重试当前步骤；完成的检查点保留。源快照、TMDB ID、语言或策略改变时，通过 `revise_package` 保守失效相关证据并要求新任务；初次 TMDB 绑定在 P2 的匹配 handler 中建立基线。

P2 生产 movie_base 任务只预建前两个步骤并在基础验证后完成；后续完整 DAG 及其门禁保留用于 P3 扩展和队列测试：

```text
match_metadata
  → base_assets_verified
  → subtitle_policy
  → final_manifest
  → archive
```

前驱检查点未完成不能跨阶段。基础门禁使用冻结且非空的 `base_required` 集合，必须包含 NFO、海报和背景图，并覆盖全部必需基础资产（含策略要求的演员头像）；成员都必须有当前版本的 SHA-256 及远程验证时间。字幕 / 最终 manifest 不进入此集合。字幕和 manifest 也有独立版本门禁。P2 已注册扫描、TMDB 匹配与基础资产 handler，没有字幕或探测 handler；archive 仍硬性拒绝。WebDAV 仅开放创建小型 NFO / JPG 与 .actors 目录，MOVE 关闭。当前 OpenList 忽略 PUT 条件头，客户端存在性检查不能替代与其它程序的原子互斥，必须独占管理刮削包。

P2 实现在 `scanner.py`、`matching.py`、`providers/tmdb.py`、`exporter.py` 和 `pipeline.py`；P3 才实现中文跳过、非中文探测 / 射手以及归档意图恢复。保留 `.actors` 与不依赖 streamdetails 的 NFO 约束。未来 handler 的远程写入必须结合租约和当前版本再次校验。

## 检查命令

```bash
uv run ruff check backend scripts/p1_*.py scripts/p2_*.py
uv run ruff format --check backend scripts/p1_*.py scripts/p2_*.py
uv run pytest -q
uv build --wheel
cd frontend
pnpm format:check
pnpm test
pnpm build
cd ..
python3.12 -m unittest discover -v
```

P0 的专用能力测试可能执行 FFmpeg / FFprobe；P1 / P2 正式任务和 P2 测试不执行它们。CI 分开运行 P0、应用后端 / 前端和开发容器检查。当前前端 Ant Design 主块约 1.09 MB（gzip 约 348 KB），构建给出体积提示但通过；后续完整 UI 再按页面拆分。Starlette 测试客户端对 HTTPX 的迁移弃用提示不影响目前锁定依赖的测试。
