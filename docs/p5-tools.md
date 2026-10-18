# P5 部署与恢复验证工具

这些工具执行测试，不发布镜像、Git 标签或 Release。实际结果见 [进度](progress.md)。所有报告、合成视频/字幕、备份和本地凭证均被 Git 忽略。

## 生产镜像与独立容器

```bash
docker build --platform linux/amd64 -t reeldock:0.1.0-local .
uv sync --frozen
uv run python scripts/p5_container_smoke.py --output reports/p5-container.json
```

需要本机 Docker、可用的回环端口 18005，以及 Python 环境中的 HTTPX。首次构建依赖网络；可按 [部署指南](deployment.md)改用官方基础镜像的公共 ECR 地址。工具使用随机名称的容器和命名卷，应用以 UID/GID 12001 运行；仅临时初始化卷属主时使用一次性 root 容器。不会读取 `.env.p0` / `.env.p2` 或导入用户配置。

检查实际生产入口、静态前端、健康检查、只读根目录、FFprobe/FFmpeg、无开发依赖、管理员初始化、配置加密、持久化暂停任务、优雅停止/重启、在线 WAL 备份、服务持锁时拒绝迁移，以及空目录恢复。之后显式挂载测试入口，复用正式 API、Worker、资产上传器、字幕和原地清单组件，注入持久化 Mock DAV / TMDB / 射手；使用真实 FFprobe 和自建两秒 MKV。

故障用例包含坏海报阻塞探测与完成、中文电影和剧集零视频读取、外语剧第 2 集无字幕保留已有刮削结果、修复后仅补失败项、清空测试缓存、服务器写入清单后丢响应并 SIGKILL 容器、重建后核对清单且全部媒体路径保持不变、MOVE 次数为零。工具 finally 仅清理自己创建的容器和命名卷；输出合成 Kodi 样例到报告同级目录。

**这证明容器内正式业务组件和受控故障恢复，不证明真实 TMDB / 射手 / WebDAV 全链路。** 报告会明确 Mock 范围。退出码 0 表示本次范围通过，2 表示失败/未验证；失败不会写固定成功。

## 专用 NAS

本地忽略的 `.env.p5` 中填写 SSH 主机、端口、用户、已有密钥（可留空使用 SSH agent）、现有专用本地父目录。版本/驱动字段可留空；镜像标签不能代替软件版本或具体存储驱动。账号不能访问 Docker 时，可在该文件填写 `P5_SUDO_PASSWORD`。保持文件权限 0600，不在聊天、命令参数或公开报告中发送密码。

```dotenv
P5_SSH_HOST=
P5_SSH_PORT=22
P5_SSH_USER=
P5_SSH_KEY=
P5_NAS_TEST_ROOT=
P5_SUDO_PASSWORD=
P5_OPENLIST_VERSION=
P5_STORAGE_DRIVER=
# NAS 容器可达的 TMDB 代理（可选，不自动复用开发机回环代理）
P5_TMDB_PROXY_URL=
```

```bash
uv run python scripts/p5_nas.py preflight
uv run python scripts/p5_nas.py validate --image reeldock:0.1.0-local
# 额外授权范围仅限 .env.p0 的专用测试父目录中的自建小包：
uv run python scripts/p5_nas.py validate --image reeldock:0.1.0-local --webdav-write
```

要求 NAS 为 amd64、Docker/Compose、Python 3 与 HTTPX。SSH 使用本任务独立 known_hosts 文件，首次记录主机密钥。sudo 密码通过 stdin 传递，不修改账号组、sudoers、现有容器、OpenList 或宿主机网络。

工具只在配置父目录中新建 `.reeldock-p5-随机ID`，传输本地已构建镜像和受控测试脚本；不发布镜像。用仓库 Compose 和独立本地 bind 目录检查真实 NAS 启动，再运行上述容器用例。NAS 端测试数据和专用镜像保留便于审阅；私有 journal 保存精确位置，不提交公开仓库。镜像传输可能耗时，事件只输出进度代码。

`p5_network.py` 通过生产 Provider 在 NAS 容器中读取真实 TMDB 详情/海报，并使用公开射手哈希查询、下载及校验字幕格式。Token 从本机 `.env.p2` 读取，通过 stdin 传入；不把开发机的回环代理当作 NAS 代理。缺凭证或网络失败明确未验证，字幕格式通过也不代表实际影片同步通过。

另外读取本地 `.env.p0` 的连接信息，通过 NAS 的实际应用镜像只读列出真实 OpenList 扫描目录和专用测试父目录，凭证通过 stdin 传递。此项零远程写入、零视频读取，不能代表完整刮削或 MOVE 已通过。真实媒体端到端、代理、具体驱动和 Kodi 项独立记录，不随 Mock 报告变成通过。

显式指定 `--webdav-write` 时复用 P3/P4 工具，从 NAS 容器在 `P0_TEST_ROOT` 下新建随机小包，执行电影/双集剧的原地基础上传、完整读回、中文跳过与清单验证。元数据明确为 Mock 中文，文件为自建极小测试字节，不代表可播放影片。当前工具不再创建归档目标或发送 MOVE；旧版归档只读恢复命令已移除。使用产品默认自动跟随 302，无须手填 115 域名，不改 OpenList 模式。此选项不会写入 `P0_SCAN_PATH` 的实际片源目录。

## Kodi

`p5_container_smoke.py` 成功后生成 `reports/p5-kodi-fixtures-随机ID/movies` 与 `tv`。全部视频、字幕和图片均为原创测试输入，NFO 由正式电影/剧集导出器生成；海报为蓝色、背景紫色。容器验证显式启用可选头像，头像为绿色；正式配置默认不下载头像。演员和 NFO 采用随片路径，不依赖 TMDB 图片 URL。

使用专用、无既有缓存的 Kodi 测试资料库，把 movies/ 和 tv/ 分别设为电影和电视剧来源，信息提供者选择 Local information only。首次扫描应能显示无 streamdetails 的 NFO、海报、背景和演员头像；剧集应有正确季集号和缩略图。修改某个自建 NFO 的 plot，刷新并确认更新，保留演员图。

真正离线验收还需让该 Kodi 实例无法访问外网，在全新资料库中首次导入与刷新。可使用专用设备或进程级网络隔离；不要更改用户整个电脑/NAS 的网络。仅选择 Local information only、仅看 XML 或命中旧缓存都不能作为断网通过。自动化组件报告中的 `kodi: unverified` 需由独立实测证据补充。

参考：[Kodi portable mode](https://kodi.wiki/view/Portable_mode)、[本地演员图片](https://kodi.wiki/view/Artwork_types#actor)。macOS 的 portable 模式仅用于隔离测试，应复制应用到临时目录，不更改现有资料库。
