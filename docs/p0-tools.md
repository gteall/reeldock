# P0 验证工具

这些命令只验证外部能力，不是正式刮削器。正式顺序仍为 TMDB → 全包基础资产上传 / 读回验证 → 字幕分流 → 最终清单 → MOVE；TMDB 中文分支仍禁止媒体探测、视频 Range、射手哈希和请求。P0 的自建中文探测样本只服务于隔离的能力测试。

## 环境与快速运行

Python 3.12、FFmpeg / FFprobe，Python 部分只有标准库，没有需要安装或锁定的第三方依赖。从仓库根目录执行：

```bash
python3.12 -m unittest discover -v
python3.12 scripts/p0.py offline --output reports/p0-offline.json
```

`offline` 只访问临时的 127.0.0.1 HTTP 服务器，生成 2 秒黑色视频 / 正弦音频和原创字幕，检查真实 FFprobe 经 Range 网关的行为，不访问互联网。需要允许本机监听端口。受限沙箱若禁止监听，会报告失败，不能将其记录为通过。没有 FFmpeg 会报告失败，不静默跳过媒体矩阵。

JSON 输出到 stdout 和 `--output`；简短总结到 stderr。每项有 `status`（passed / failed / unverified）、`evidence` 和错误代码。退出码：**0** 表示本次选择的检查均通过，**1** 表示至少一项失败，**2** 表示没有失败但存在未验证项。离线完整报告通常返回 2，因为真实 NAS / Kodi 没有在离线运行中验证。`p0_complete` 始终为 false：单次命令不能代替完整阶段验收，整体结论在 compatibility.md 中人工汇总。

## 本地配置与真实 WebDAV

```bash
cp .env.example .env.p0
chmod 600 .env.p0
```

用编辑器填写 `.env.p0`，不要 `source` 它；解析器将值视为字面量，不执行命令、变量插值或反引号。系统环境变量优先于文件。省略 `--env-file` 时，自动读取当前目录存在的 `.env.p0`。账号、报告、临时 MOVE 意图、生成媒体均已忽略，不提交到 Git。

| 字段 | 含义 |
| --- | --- |
| P0_WEBDAV_URL | 实际 WebDAV 端点，例如 `https://nas.example/dav/`，不是 OpenList 网页首页；URL 不嵌入账号密码 |
| P0_WEBDAV_USERNAME / PASSWORD | 有相应读取 / 写入 / MOVE 权限的测试账号 |
| P0_SCAN_PATH | 相对 WebDAV 根的已解码绝对路径，例如 `/测试片库`，不是本机路径，不重复 `/dav` |
| P0_TEST_ROOT | 现有、明确用于测试的父目录，不能是 `/`；所有写入只进入新建的随机子目录 |
| P0_REDIRECT_HOSTS | 内容下载重定向的精确主机名白名单，逗号分隔；没有通配符；先核对实际存储下载域名 |
| P0_REMOTE_MEDIA / LOCAL_MEDIA | 可选远程受控媒体路径及其相同的本地副本，用于真实片源的四块指纹比较 |
| P0_TIMEOUT_SECONDS | 单次网络 I/O 超时，默认 15 秒，范围 (0,60] |
| P0_PROBE_TIMEOUT_SECONDS / PROBE_MAX_BYTES | FFprobe 总时限 / 源文件读取字节预算，默认 45 秒 / 64 MiB |
| P0_FFPROBE / FFMPEG | 可选可执行文件名或路径 |
| P0_SHOOTER_URL / HASH / FILENAME | 默认公开测试哈希，不需要 TMDB Key；自定义哈希时仅提交自己授权的指纹和文件名 |

```bash
# 只读扫描 + 射手公开样本；不会 PUT / MKCOL / MOVE
python3.12 scripts/p0.py live --env-file .env.p0 --shooter

# 写入自建小文件，远程与本地四块指纹比较，目录 MOVE、目标冲突
python3.12 scripts/p0.py live --env-file .env.p0 --allow-write --shooter

# 同时上传四个极小的自建 MKV/MP4，在真实 WebDAV 下载链路探测
python3.12 scripts/p0.py live --env-file .env.p0 --allow-write --media-matrix --shooter

# 对明确配置的远程样本启用有预算的独立探测
python3.12 scripts/p0.py live --env-file .env.p0 --probe
```

写测试创建 `.reeldock-p0-<32位随机ID>`，不覆盖已存在的运行目录，不发 DELETE，不移动待刮削 / 已刮削目录中的用户媒体。每次生成新目录，保留产物供核对；清理时只人工删除确认过的测试子目录。工具先尝试 PUT 小文件并完整读回；每个写入步骤出错会明确失败，后续独立检查可能继续产生测试产物。

下载跨源重定向仅对白名单放行，移除 Authorization / Cookie / Proxy-Authorization，也移除只适用于原资源的 If-Match / If-Unmodified-Since / If-Range；不把 DAV 的 ETag 强加给 CDN，保留读取前后 DAV 快照核对。禁止 HTTPS 降级、写请求重定向。精确检查 206、Content-Range、Content-Length 与实际读取长度；服务器忽略 Range 返回 200 时，在消费响应体前拒绝。四块指纹只读取 16 KiB，另有 PROPFIND 的 XML 流量。MD5 只用于兼容射手协议，上传读回使用 SHA-256。

FFprobe 不接触 WebDAV 密码或签名链接，只访问随机令牌保护的临时回环网关；网关将请求拆成最多 1 MiB 的有界 Range，统一计算请求字节预算。报告记录 `bytes_requested`（媒体内容预算，含重复请求，不含 XML / HTTP 头）、请求数和耗时。小样本可能全部落入预算读取范围；这不允许回退到全文下载大型真实视频。超预算、超时、错误范围或源快照变化均失败。流语言 / forced 标签不等于内容简繁或完整性判定；P3 才实现业务字幕判定。

## MOVE 超时恢复

MOVE 前在 `reports/move-intent-*.json` 以 0600 独占创建意图并 fsync，包含源目标相对路径、随机包身份和小文件内容，不含账号。MOVE 使用 `Overwrite: F`，207 逐项解析后阻塞。网络超时后只核对源 / 目标、身份、文件集合和全部内容，不重复 MOVE、不盲删源。

```bash
python3.12 scripts/p0.py reconcile --env-file .env.p0 \
  --journal reports/move-intent-替换为实际ID.json
```

只有源消失、目标身份及完整文件集合一致才返回 moved_verified。源仍在、两边都有、两边都无、身份或文件异常均失败并保留现场。模拟服务器覆盖移动前超时、移动成功后丢响应、207 部分失败和权限拒绝。不会在真实 NAS 上故意中断连接制造故障；真实故障能力仍需专门环境验证。

## Kodi 导入、刷新、断网

```bash
python3.12 scripts/p0.py kodi-fixtures --directory fixtures/kodi
```

生成的 `ReelDock P0 Test <ID> (2026)` 电影目录包含自建视频、同名基础 NFO、poster.jpg、fanart.jpg、`.actors/ReelDock_Test_Actor_<ID>.jpg`。每次演员名称唯一，避免旧缓存造成假通过；NFO 无 streamdetails、thumb 远程 URL，也不伪造媒体参数。`generated/` 是探测样本工作目录，**不要将整个 fixtures/kodi 添加为片库**，只添加本次生成的电影目录，或将其放入独立的测试片库父目录。

1. 在 Kodi 添加专用源，内容选择电影、信息提供者 **Local information only**。源若就是单电影目录，启用“电影在以片名命名的单独目录中”和“选定文件夹包含单个视频”，关闭嵌套扫描。
2. 扫描并打开影片信息，确认 NFO 中的标题 / 简介、蓝色海报、紫色背景、绿色演员头像。记录 Kodi 版本 / 皮肤以及成功与否。必要时检查 Kodi 的演员缩略图设置，不能用远程图片补齐假通过。
3. 修改生成 NFO 的 plot 为独特文本，影片信息 → 刷新，选择使用本地信息，确认文本更新且随片图片仍在。
4. 真正阻断播放端外网、保留访问本地 / NAS 测试路径的能力，生成**新 ID** 样例并首次导入，再刷新，确认未曾缓存的演员图片仍显示。只切换 Local information only 或复看已缓存图片不算断网验收。
5. 将 `kodi-observation.example.json` 复制为 `kodi-observation.json`，填写版本；每项 `true` 表示实测通过、`false` 表示实测失败、`null` 表示未验证。执行：

```bash
python3.12 scripts/p0.py kodi-check --observation fixtures/kodi/kodi-observation.json
```

该命令校验并逐项输出人工记录，`evidence=manual_attestation`，不自动操作 Kodi、不测量网络隔离。没有实际观察保留 null，不能改成 true。字幕同步、实际影片命中率、NAS 端 Docker 和其它播放端均不由这个样例证明。
