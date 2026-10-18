# Docker / 飞牛部署

本文件对应 P5 候选构建；当前版本仍为开发版本，没有发布 v0.1.0 镜像或 Release。实际验证与未验证项见 [进度](progress.md)和[兼容范围](compatibility.md)。只验证 `linux/amd64`；不宣称 arm64 已支持。

## 首次启动

安装 Docker Engine 和 Compose v2 或更新版本。将仓库放到 NAS 本地磁盘，创建专用数据目录，不将 `/data` 放到 WebDAV、SMB、NFS 或其它网络文件系统。

当前源码位于 `main` 分支。首次获取源码可执行：

```bash
git clone --branch main https://github.com/gteall/reeldock.git
cd reeldock
```

```bash
cp .env.example .env
mkdir -p data cache
# 使用 NAS 上实际运行影坞的非 root UID/GID；以下与默认值一致。
sudo chown 10001:10001 data cache
chmod 700 data cache
```

在 `.env` 中修改以下配置，`REELDOCK_UID` 必须非 0：

| 配置 | 含义 |
| --- | --- |
| `REELDOCK_IMAGE` | 默认 `reeldock:0.1.0-local`，由当前源码本机构建；不是已存在的 GHCR 发布镜像 |
| `REELDOCK_UID` / `REELDOCK_GID` | 容器应用运行身份；绑定目录预先赋予相同属主；无需以 root 启动应用 |
| `REELDOCK_DATA_PATH` | NAS 本地目录，保存 SQLite、WAL、master.key、实例锁及自行创建的备份 |
| `REELDOCK_CACHE_PATH` | 独立本地缓存目录，可重建；丢失后预览可能需重新读回/局部重试 |
| `REELDOCK_BIND_IP` / `REELDOCK_PORT` | 默认 `127.0.0.1:8765`；需要局域网直连时将绑定改为 `0.0.0.0` |
| `REELDOCK_ALLOWED_ORIGINS` | 浏览器实际来源的 JSON 数组，包含协议、主机和端口；NAS 访问时必须替换/补充，如 `["http://nas.example:8765"]` |
| `REELDOCK_SECURE_COOKIE` | HTTP 为 false；由 HTTPS 访问时为 true |
| `REELDOCK_WORKER_ENABLED` | 默认 true；恢复旧备份后可先设 false，仅检查状态，再启用任务 |

Compose 的 bind mount 禁止自动创建宿主目录；路径或权限错误时会直接失败。不要通过改成 root 或 777 掩盖路径问题。

```bash
docker compose --env-file .env config --quiet
docker compose build reeldock
docker compose up -d --no-build
docker compose exec reeldock reeldock init-admin
docker compose ps
```

初始化交互式输入至少 12 位密码，无默认密码；已初始化的账号不能重复创建。应用已运行时也可初始化管理员，不在此操作中重复迁移。访问 `.env` 对应地址登录；`/api/health` 为本机健康检查，包含数据库连通性。

如果 Docker Hub 拉取失败，可使用相同官方基础镜像的公共 ECR 来源，不修改 Docker 全局配置：

```bash
docker compose build \
  --build-arg NODE_IMAGE=public.ecr.aws/docker/library/node:22-bookworm-slim \
  --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim-bookworm \
  reeldock
docker compose up -d --no-build
```

停止用 `docker compose stop`，重启用 `docker compose restart reeldock`。容器可重建，`/data` 和主密钥必须保留。不要运行两个容器共享同一个 `/data`；实例锁会拒绝第二个实例。

## OpenList、302 和代理

设置页保存 WebDAV URL（一般以 `/dav/` 结尾）、凭证、两个现有且分离的输入/输出目录及 TMDB 凭证。`.env.p0` / `.env.p2` 仅用于验证工具，不自动导入应用。先用专用测试媒体包操作；电影一片一目录，剧集按剧根目录归包。

同 NAS 的容器中 `127.0.0.1` 指影坞容器自身。可使用 NAS 可路由地址，或让两个容器加入同一 Docker 网络，以 OpenList 的服务名和实际端口访问。OpenList 不需要中转，保留 **302 直链**。下载重定向主机限制默认留空；客户端跟随所配置 WebDAV 返回的动态下载位置，跨域不携带 WebDAV 凭证和 Cookie。

如果已有共享网络，可新建单独的覆盖文件，保持默认 Compose 可独立运行：

```yaml
# compose.openlist.yaml
services:
  reeldock:
    networks: [default, openlist]
networks:
  openlist:
    external: true
    name: openlist_shared  # 替换为已经存在的网络
```

```bash
docker compose -f compose.yaml -f compose.openlist.yaml up -d --no-build
```

此时 WebDAV 示例为 `http://openlist:5244/dav/`，主机名和端口需与实际 OpenList 部署一致。输入/输出须在已验证的同一存储驱动范围；同主机名不能证明跨挂载 MOVE 安全。MOVE 默认关闭，在专用目录验证后才确认对应范围，目标已有目录不合并或覆盖。

WebDAV 能直连时，通用代理留空，TMDB 使用专用代理。代理若运行在宿主机，不要填写容器自身的 `127.0.0.1`；使用容器可访问的 NAS 地址，或代理容器的共享网络服务名。应用只使用设置页保存的代理，不自动读取系统代理。射手下载主机限制与 WebDAV 主机限制是两套独立规则。

## HTTPS / 反向代理

将反向代理指向 NAS 的影坞端口；可将容器端口仅绑定回环。将浏览器 HTTPS 来源加入 `REELDOCK_ALLOWED_ORIGINS`，启用 secure cookie。应用不信任外部转发 IP 头，不开放跨站 CORS。

SSE 路径 `/api/events/stream` 需要关闭缓冲、允许长连接；Nginx 示例：

```nginx
location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 120s;
    proxy_set_header Host $host;
}
```

## 运行维护

生产镜像使用多阶段构建，不包含 Node、uv、测试依赖、源码工作区或本地凭证。运行时包含 FFprobe / FFmpeg。Compose 使用非 root UID/GID、只读根文件系统、独立 tmpfs、去除 capabilities、禁止提权；无需特权、FUSE、GPU、媒体盘挂载、Docker socket 或 Redis。

日志输出为脱敏事件代码，Docker `json-file` 每文件 10 MiB、最多 3 文件。`docker compose logs --tail 100 reeldock` 查看最近事件，不需要打开 HTTP 请求访问日志。内存限制默认 1 GiB，PID 限制 128；大库或更高并发需按实际预算调整。临时目录 256 MiB，不用于缓存整视频。

收到 SIGTERM 后停止领取新任务；HTTP/SSE 最多等 20 秒，随后停止 Worker、终止正在执行的探测、释放租约并保存中断状态。Compose 给出 60 秒退出时间。当前锁定 Uvicorn 在完成优雅关闭后重新抛出 SIGTERM，Docker 可记录退出码 143；验收同时检查状态保留与恢复。若直接断电 / SIGKILL，等待持久化租约到期后恢复；已有 MOVE 意图先核对两侧，不盲目重发。

备份、恢复与升级命令见 [升级与回退](upgrade.md)。本机源码运行与旧 `/data/cache` 布局保持兼容；生产 Compose 显式使用 `/cache`。

依据：[Compose 服务配置](https://docs.docker.com/reference/compose-file/services/)。本文件是项目部署契约，不代表飞牛实机已经验收。
