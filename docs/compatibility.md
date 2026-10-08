# 兼容性与验证边界

P0 验证日期：2026-10-08；P1 补充：2026-10-09。P0 工具已实施，**完整 P0 仍未验收完成**。真实、Mock 与未验证结果分开；本机能访问 NAS 不代表飞牛 Docker 容器已通过。运行方法见 [P0 工具说明](p0-tools.md)与 [P1 开发指南](development.md)，交接见 [进度记录](progress.md)。本文件不记录个人 NAS 地址、路径、账号、签名 URL 或第三方字幕正文。

## 实测环境

| 项目 | 本次环境 / 边界 |
| --- | --- |
| 工具运行端 | macOS 开发机，Python 3.12.13 |
| FFmpeg / FFprobe | 8.1.1；自建 MPEG-4 + AAC、MKV SubRip / MP4 mov_text |
| WebDAV | 用户配置的 OpenList 服务，通过 `/dav/` 访问；专用测试父目录下随机子目录 |
| 存储下载链路 | 实际观测到 115 CDN HTTPS 重定向；精确 OpenList 版本 / 驱动名称尚未提供，不能推断整个 115 驱动族都兼容 |
| Kodi | 本机 21.3，Estuary 皮肤，本地专用样例源，Local information only |
| 飞牛 / Docker | P0 未部署；P1 在本机 Docker 完成开发镜像构建、健康检查与重启持久化；飞牛及容器访问真实 NAS 仍未验证 |

## 检查矩阵

| 能力 | 真实检查 | 离线 / Mock | 结论与限制 |
| --- | --- | --- | --- |
| PROPFIND Depth:1 / stat | 通过 | 通过 | 能列出配置目录，解析逐项 propstat；不等于扫描器和稳定窗口已经实现 |
| Unicode、空格、#、% 文件名 PUT / 读回 | 通过 | 通过 | 小 NFO 完整 SHA-256 一致；仅自建产物 |
| 302 与跨域凭证剥离 | 302 / 下载通过；凭证剥离由 Mock 验证 | 通过 | 精确白名单；Range 保留，认证和原资源条件头移除；未抓取真实 CDN 入站头 |
| 远程 / 本地射手四块指纹 | 通过 | 通过 | 合成 131,077 字节样本；4 × 4 KiB；真实用户片源未提供 |
| 200 忽略 Range、错 Content-Range、短读 | 未在真实 NAS 注入 | 通过 | 200 / 错范围在消费视频响应体前拒绝；截断响应阻塞 |
| MKV / MP4 / 尾部索引 FFprobe | 通过（NAS 上的自建样本） | 通过（真实 FFprobe + Mock HTTP） | 有总时限、字节预算、前后源快照核对；大型真实媒体未验证 |
| 射手候选查询与字幕下载 | 通过（本机网络） | 通过 | 公开测试哈希：3 个 ASS 候选、56,379 字节、461 个 Dialogue；未核对用户视频语言 / 同步 / 命中率 |
| 射手无匹配 / 503 / 超时 | 未主动制造真实故障 | 通过 | 单字节 0xff、HTTP 错误、网络超时均不会伪装成功 |
| 目录 MOVE + 身份 / 文件核对 | 通过 | 通过 | 自建小包，Overwrite:F；未证明跨挂载或大媒体包原子性 |
| MOVE 目标存在 | 返回 500，双方哨兵完整保留 | 412 / 非标准 500 均覆盖 | 安全拒绝通过，协议响应非标准；不能将一般 HTTP 500 当移动成功 |
| MOVE 超时 / 部分失败 / 权限拒绝 | 未在真实 NAS 注入 | 通过 | 移动后丢响应可核对；移动前超时、207 部分失败、两边都有等保留现场 |
| Kodi 基础 NFO 无 streamdetails 首次导入 | 通过（本机 UI） | XML / 样例生成通过 | 标题 / 简介可读；Kodi 自己可读取媒体参数，不表示 ReelDock 执行了探测 |
| Kodi poster / fanart / .actors JPG | 通过（本机 UI） | JPEG / 路径检查通过 | 蓝海报、紫背景、绿头像；NFO 无远程 thumb，演员使用唯一名字排除旧演员缓存 |
| Kodi 本地 NFO 刷新 | 通过（本机 UI） | 有操作步骤 / 记录校验 | 修改 plot 后显示 `ReelDock P0 REFRESH CHECK 2026-10-08`，头像保留 |
| Kodi 真正断网、无缓存首次导入 / 刷新 | **未验证** | 提供步骤，不能用 Mock 替代 | 本次未切断播放端外网，Local information only 不等于断网 |

## 关键兼容性发现

1. OpenList 网页首页会对 PROPFIND 返回 405；同服务器的 `/dav/` 返回 207。已只在本地忽略配置中修正端点。
2. 内容 GET 会 302 到带签名的 HTTPS 下载地址。工具默认拒绝未知跨源主机，核对实际下载主机后才加入本地精确白名单，且不转发 WebDAV 账号 / Cookie，不提交下载 URL。
3. DAV ETag 与 CDN 资源 ETag 不可直接混用。本次把 DAV If-Match 转发到 CDN 时得到 412；修复后跨源移除这些条件头，保留读取前后 DAV 快照检查。该方案无法提供跨两个服务的原子读取承诺，正式 P3 仍须稳定窗口 / 快照变化阻塞。
4. 正常目录 MOVE 能成功。目标存在时后端返回 500，而非预期的 409 / 412。验证工具只在独立冲突用例中核对双方哨兵并记录 `standard_conflict_status=false`，正式归档仍需提前检查目标，未知错误不重试、不覆盖、不删源。
5. Kodi 的“文件”浏览视图与“电影资料库”视图使用图片方式不同。资料库中的海报、影片信息中的演员图与“显示同人画”中的背景已实测；不能单凭文件列表中的通用图标判定资产导入失败。

协议依据：[WebDAV MOVE RFC 4918](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9)、[FFprobe 文档](https://ffmpeg.org/ffprobe.html)、[Kodi 电影 NFO](https://kodi.wiki/view/NFO_files/Movies)、[Kodi 演员图片约定](https://kodi.wiki/view/Artwork_types#actor)。射手指纹与公开样本来自 [ChineseSubFinder 实现](https://github.com/ChineseSubFinder/ChineseSubFinder/blob/master/pkg/logic/sub_supplier/shooter/shooter.go)，不是射手官方持续服务承诺。

## 样本与预算

离线自建样本仅 2 秒，音频为正弦波，语言标签是测试输入，不表示人工识别了真实配音。简体、繁体文本由生成器原创，forced 为容器标记；本阶段不据此实现完整字幕 / 简繁业务判定。

| 自建样本 | 音频标签 | 字幕 | 媒体读取量（Mock / 真实均相同） | Mock / 真实 WebDAV 耗时（一次观测） |
| --- | --- | --- | --- | --- |
| chinese.mkv | chi，default | 简体，SubRip | 27,448 B，1 Range | 约 0.08 / 1.894 秒 |
| foreign-simplified.mkv | eng，default | 简体，SubRip | 27,448 B，1 Range | 约 0.08 / 1.845 秒 |
| foreign-traditional-forced.mkv | eng，default | 繁体，SubRip，forced | 27,452 B，1 Range | 约 0.08 / 2.022 秒 |
| tail-index.mp4 | eng，default | 英文，mov_text | 28,576 B，1 Range | 约 0.08 / 2.200 秒 |

生成器和测试确认 MP4 顶层 moov 位于 mdat 之后；样本太小，读取一次即能覆盖整个文件，**没有证明数 GB MP4 的真实远端 seek 成本**。预算检查在读取源视频前预留字节，1 字节预算用例验证零视频 GET；默认预算 64 MiB、45 秒。JSON 报告记录每次真实耗时 / 字节，不应把此表的本机数值当作 NAS 性能保证。

## 未验证与不支持

- 待验证：精确 OpenList / 驱动版本、目标飞牛环境、代表性大型片源及配套本地副本、真实权限 / 部分失败 / 超时故障、Kodi 外网断开且无缓存的新样例。
- P0 不支持并阻塞：Range 返回 200、错误范围、未知下载主机、HTTPS 降级、写请求重定向、主体超预算、源快照变化、未知 MOVE 结果。
- 不实现自动整视频复制上传删除、不覆盖 / 合并归档目标、不删除任何远程测试文件；跨存储驱动归档未声明支持。
- P1 已提供产品 API / UI / 持久化 Worker / 开发镜像；正式刮削流水线尚待 P2 / P3，飞牛发布待 P5。中文业务规则没有因 P0 探测测试而变化。

本地证据文件位于忽略的 `reports/`：离线报告、真实 WebDAV / 射手报告、Kodi 观察报告和 MOVE 意图。报告不包含凭证 / 第三方正文，但 MOVE 意图含私有相对路径，不应上传公开仓库。

## P1 验证补充

- 新的 HTTPX WebDAV Provider 在本机使用既有忽略配置，实际列出扫描目录（2 项）和专用测试父目录（3 项）；零媒体内容读取、零写入、零 MOVE。只证明本机只读连接，不证明正式归档能力。
- 自动化覆盖逐项权限失败、允许的可选属性 404、特殊路径、跨源认证 / Cookie / 条件头剥离、未知下载主机与 HTTPS 降级拒绝，以及 Range 200 / 错范围在消费正文前拒绝。代理配置已接线，真实代理链路未验证。
- 本机生成式 Mock 经过真实 HTTPX → Worker → SQLite → REST → React 链路完成只读连接任务，检查点为输入 1 项 / 输出 0 项；浏览器实际验证登录、无效路径拒绝、保存后刷新和任务详情。此样例不代表真实媒体刮削。
- 开发镜像在本机 Docker 构建与启动通过，独立容器 / 命名卷的配置、会话、暂停任务和事件经真实重启保留。Docker Hub 鉴权端点最初超时，公共 ECR 基础镜像来源构建通过；没有修改用户 Docker 全局网络配置。
- 媒体探测、TMDB、射手和归档生产 handler 未启用；P1 的 PUT / MOVE 双重关闭。Kodi 和大媒体的 P0 待验项保持未验证；没有在飞牛 NAS 运行本应用。
