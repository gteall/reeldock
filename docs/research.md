# 外部接口调研与验证记录

调研日期：2026-10-08。优先引用服务官方文档；射手部分区分历史协议参考实现与本次实际网络测试。外部接口和 NAS 软件会变化，实现前及发布前应复核。

## TMDB

- [API FAQ](https://developer.themoviedb.org/docs/faq)：API 覆盖影视及人物数据 / 图片；非商业使用需提供来源标识，关于页应包含批准的 Logo 和要求的声明。商业场景另按 TMDB 许可处理。
- [图片 URL 构造](https://developer.themoviedb.org/docs/image-basics)：使用 configuration 提供的基址 / 规格与资源 path 构造图片 URL。
- [图片语言](https://developer.themoviedb.org/docs/image-languages)及[电影图片接口](https://developer.themoviedb.org/reference/movie-images)：图片语言过滤会影响候选，设计需显式考虑多语言 / 无语言回退。
- [电影演职员](https://developer.themoviedb.org/reference/movie-credits)及[单集详情](https://developer.themoviedb.org/reference/tv-episode-details)：电影与剧集分别适配到统一领域对象。
- [电影详情](https://developer.themoviedb.org/reference/movie-details)、[剧级详情](https://developer.themoviedb.org/reference/tv-series-details)、[官方 OpenAPI](https://developer.themoviedb.org/openapi/tmdb-api.json)和[语言说明](https://developer.themoviedb.org/docs/languages)：实现区分详情的 original_language 与请求本地化语言参数。按用户最新规则，基础资产完成后以 original_language 分流，中文集合默认 zh / cn；这是项目业务策略，不是从原始语言推导制片国家或实际文件音轨。
- [限流说明](https://developer.themoviedb.org/docs/rate-limiting)：处理 429，不把旧限流机制当作当前固定额度。

本次未提供 TMDB 凭证，未完成带凭证的搜索、详情及图片下载测试；本次 P0 不调用 TMDB，带凭证闭环留在 P2。

## 射手字幕

参考 [ChineseSubFinder 的射手 Provider 源码](https://github.com/ChineseSubFinder/ChineseSubFinder/blob/master/pkg/logic/sub_supplier/shooter/shooter.go)。这是该开源项目的实际实现，**不等于射手官方对接口维护或可用性的承诺**。代码包含四段 MD5 指纹算法、POST 表单字段和公开测试哈希；[历史 JSON 客户端](https://github.com/qzane/SPlayerSubDownloader)也记录了接口结构。

本次仅使用上述源码附带的公开测试文件名 `S05E09.mkv` 和测试哈希，没有读取或提交用户片源：

| 检查 | 本机观测结果 |
| --- | --- |
| HTTPS GET API 路径，不带查询参数 | HTTP 200，响应单字节 `0xff` |
| POST 公开测试哈希，format=json，lang=Chn | HTTP 200，JSON 数组，3 个 ASS 候选 |
| 下载其中首个候选 | HTTP 200，56,379 字节 |
| 格式检查 | 包含 ASS Script Info 和 Events，461 行 Dialogue |

该观测证明一个公开样本在本机当时能完成查询及下载。没有用户视频可供时间轴 / 语言核对，也没有 NAS 网络环境，故不能据此判断实际片库命中率、简体覆盖率或长期稳定性。未将下载字幕、临时链接及响应正文提交到仓库。

实现重点：Range 哈希一致性、候选选择、返回空结果 / 单字节无结果标记、文本解析、字符编码、延时字段、下载链接过期，以及人工补齐后重新校验。未命中必须保持阻塞，不能自动放宽用户定义的成功条件。

## OpenList / WebDAV

- [OpenList WebDAV 配置与权限](https://pages.doc.oplist.org/guide/advanced/webdav)：读取与管理权限分开，写操作还需相应文件操作权限；通常地址为 `/dav/`。
- [OpenList WebDAV 驱动与重定向](https://doc.oplist.org/guide/drivers/webdav)：实际内容可能经 302 返回下载地址，需设计重定向和 Range 检查。
- [WebDAV RFC 4918](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9)：MOVE 使用 Destination，Overwrite: F 防止替换目标，目录操作可能出现 207 子项失败。规范不证明某个 OpenList 驱动实现了原子移动。

P0 已使用用户提供的本地配置，在专用测试目录完成真实读写、Range 和 MOVE；发现 DAV / CDN ETag 差异及目标冲突返回 500，详见 [兼容性记录](compatibility.md)。实际地址 / 路径不写入仓库，精确 OpenList / 驱动版本仍待提供。

## Kodi 与其它播放端

- [Kodi 演员图片约定](https://kodi.wiki/view/Artwork_types#actor)：电影 / 剧目录中的 `.actors` 保存按演员姓名命名的头像。
- [Kodi 剧集 NFO](https://kodi.wiki/view/NFO_files/TV_shows)及[季图片](https://kodi.wiki/view/Artwork/Season)：首版导出剧级与集级 NFO，季海报位于剧集根目录，不将 `season.nfo` 设为 Kodi 必需项。
- [Kodi 本地演员图片讨论与官方团队说明](https://forum.kodi.tv/showthread.php?tid=363148)：远程 thumb URL 和本地演员图的使用需关注实际配置与刷新行为。
- [Kodi 本地头像与远程 thumb 的问题记录](https://github.com/xbmc/xbmc/issues/25662)：即使有本地图，远程 thumb 仍可能影响结果；因此方案不默认写入 TMDB 头像 URL，并要求断网 / 无缓存导入验收。
- [飞牛影视媒体库设置](https://help.fnnas.com/articles/v1/media/media_install)：可优先读取本地 NFO 与图片，但不能由此推断它完全兼容 Kodi 的演员图片约定。

用户已选择 Kodi 为首要目标，首版优先使用随媒体保存的 `.actors`。P0 已在本机 Kodi 21.3 / Estuary 完成基础 NFO、随片图片 / 演员 JPG 的导入及刷新；真正断网、无缓存的新样例导入尚未验证，其它播放端另设兼容配置。

## 运行环境

- [FFprobe](https://ffmpeg.org/ffprobe.html)与[FFmpeg HTTP 协议](https://ffmpeg.org/ffmpeg-protocols.html#http)：用于远程媒体结构探测，应用额外设置子进程超时和传输字节预算。
- [SQLite WAL](https://www.sqlite.org/wal.html)：数据库与 WAL 持久化在本机磁盘，不放 WebDAV 等网络文件系统。
- [Docker Compose](https://docs.docker.com/compose/)及[单机生产部署](https://docs.docker.com/compose/how-tos/production/)：以 Compose 交付一个应用服务和本地数据卷；飞牛设备上的操作和网络连通性仍需实测。

正式部署不要求额外数据库、Redis、FUSE 或 GPU。未提供目标设备架构，先将 amd64 设为首期验收目标，arm64 在对应镜像与设备验证后声明支持。
