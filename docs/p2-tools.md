# P2 电影基础刮削与验证

P2 的生产任务只执行 `match_metadata → base_assets_verified`，完成后结束。字幕显示“尚未进入字幕阶段”，实际音轨显示“未探测”；不创建最终 manifest，不读取视频内容，不调用 FFprobe / FFmpeg、射手或 MOVE。

## 在本机使用

按 [开发指南](development.md)安装依赖、初始化管理员、构建前端并启动 API。在设置页保存 WebDAV、独立的输入 / 输出目录及 TMDB Token / API Key；需要时填写 **TMDB 专用代理**，使 WebDAV 保持直连。通用代理仍作用于 WebDAV 和未设置专用代理的 TMDB。

1. 在“电影与资产”点击“扫描目录”。扫描只通过 PROPFIND / stat 登记路径、大小、ETag / 修改时间及已有资产，不 GET 视频或字幕。
2. 首次出现“等待稳定”。默认 600 秒后再次扫描，媒体集合、大小和版本一致才显示“已稳定”。改变配置或源文件会重新等待；暂不定时自动扫描。
3. 点击“基础刮削”。文件名 `{tmdb-550}`、`[tmdbid=550]`、IMDb ID 或有效现存 NFO 的明确 ID 优先。否则解析片名 / 年份，搜索并评分。标题和年份精确吻合且没有近似候选才自动选定；歧义、缺年份或年份冲突需要人工选择。
4. 在详情中选择候选或保存 **电影** TMDB ID，再启动新任务。现存 NFO / 文件名的明确 ID 冲突仍会阻塞；人工选择不是覆盖保护的开关。更换 ID 会使旧检查点失效。
5. 查看 NFO、海报、背景图、演员头像的逐项状态、预览和错误。点击“重试失败项”只补未验证项；暂停 / 重启也保留本地缓存与上传检查点。源 / 配置 / ID 变化时应重新扫描、启动新任务。

当前支持独立目录中的单个常规电影视频。散放视频、多视频混装、疑似剧集、含不明确子目录的媒体包会进入待确认；下载临时文件、sample、extras、trailers 不作为电影。媒体格式通过扩展名识别，不代表容器内容已经探测。遍历限制为深度 8、2,000 个目录、20,000 个条目，超限明确失败，不把截断清单当成完整扫描。

## 文本、图片与演员策略

TMDB 详情首先请求 `zh-CN`，缺失字段按作品原始语言、`en-US` 逐字段补齐；已有中文字段不被回退文本覆盖。`original_language` 独立持久化，只来自主详情，不从请求参数、制片国家、中文标题或 spoken_languages 推导。图片按中文、无语言、作品原始语言、英文及其余来源排序；某候选损坏时最多尝试 5 个候选。

搜索、详情、演职员、图片和 configuration 存 SQLite TTL 缓存，图片通过解码验证后存入本地内容寻址缓存，多个电影共享来源图片缓存。429 尊重有限的 Retry-After，长等待交回持久化任务重试，避免无限阻塞。浏览器只访问经过登录校验的后端缓存预览，不接触 TMDB Token 或 WebDAV 下载 URL。

默认前 20 位演员中来源有 profile 者为必需；来源无图显示 `source_no_image`，不当成下载成功。范围设为 0 表示全部，未选演员明确显示“未纳入本次要求”。`strict` 要求所选演员都有头像；缺图时可在 WebDAV 手工放入对应 JPG 后重试。名字空格转下划线，重名 / 大小写碰撞、非法文件名会阻塞，不擅自加 ID 破坏 Kodi 的匹配约定。

生成与视频同 stem 的 UTF-8 NFO、`poster.jpg`、`fanart.jpg` 和 `.actors/演员名.jpg`。NFO 保留 TMDB 事实，正确转义 XML；不生成未知 streamdetails，不伪造实际编码、音轨或时长，不默认输出演员远程 thumb。官方约定参考 [Kodi 电影 NFO](https://kodi.wiki/view/NFO_files/Movies)、[TMDB 图片语言](https://developer.themoviedb.org/docs/image-languages)。

## 已有资产与恢复边界

有效且 ID 一致的 NFO **原样复用**，包括人工标题、未知扩展字段、playcount、resume、userrating 和原有演员内容。任何现存 NFO 在解释前存入不可变本地缓存备份，远程原件不被替换；缺明确 ID、多个 NFO、类型 / ID 冲突或无效 XML 时保留原件并阻塞。人工修复 NFO 后重新扫描 / 启动即可。P2 不提供强制覆盖开关，因此受保护的 NFO 内容也不会自动同步更新的 TMDB 字段。

现存可解码图片可作为人工资产复用，无需再从 TMDB 下载；缺少来源的 poster / fanart 也可通过 WebDAV 人工补齐。扫描发现已验证资产的版本 / 大小改变或消失时撤销该项完成状态。影坞生成的旧 ID 图片不会自动重绑到新电影：需人工核对、补图或移除旧文件后重试。

统一上传器先持久化内容缓存 / SHA-256，再提交小文件，并完整 GET 读回比对。服务器已完成 PUT 但响应超时 / 应用退出时，恢复先核对目标内容；已一致则复用，不盲目再 PUT。坏图片和内容不一致的目标不会被覆盖或误计成功。基础必需集合在上传前冻结，只包含 NFO、poster、fanart 和演员策略要求的头像，不含字幕 / 最终 manifest。

**已发现的真实限制：当前 OpenList 对 PUT 忽略 `If-None-Match: *`（返回 201）。** Provider 额外在 PUT 前 stat，已存在则客户端拒绝写入，仍发送条件头；包租约排除影坞自身并发。但这不是对其它程序的原子 CAS：刮削期间须独占管理当前包，不能同时由其它工具修改同一路径。影坞不通过覆盖、删除或视频复制掩盖此限制。详见 [兼容性记录](compatibility.md)。

`/data/cache/assets`（本机默认 `data/cache/assets`）存图片、NFO 及原件缓存；SQLite 保存哈希和远程验证版本。缓存丢失时可从远程小型资产恢复，但不要把可清理缓存当成唯一人工数据备份。包租约和上下文在网络工作前后复核；最终基础成功前再次核对视频**文件信息**快照。P2 的完成状态不意味着字幕已满足、可以归档或片源经过内容验证。

## 可重复验证工具

本地凭证文件 `.env.p2` 已由 `.gitignore` 和 `.dockerignore` 排除。其内容示例：

```dotenv
P2_TMDB_TOKEN=
P2_PROXY_URL=
```

Token 不带 `Bearer ` 前缀；也接受 API Key。上述文件仅供验证工具使用，**不会自动导入应用配置**；应用凭证需在登录后的设置页保存。

```bash
uv run python scripts/p2_verify.py mock --output reports/p2-mock.json
uv run python scripts/p2_verify.py tmdb --tmdb-env .env.p2 --movie-id 550 --output reports/p2-tmdb.json
uv run python scripts/p2_verify.py webdav --webdav-env .env.p0 --output reports/p2-webdav.json
```

- `mock`：运行 P2 扫描、真实 Provider HTTP Mock、API / Worker 闭环和故障矩阵；禁止启动媒体子进程、访问射手、GET 视频 / 字幕或 MOVE。
- `tmdb`：真实只读详情、中文字段、configuration、海报 / 背景 / 一个头像下载与解码、缓存复用。
- `webdav`：只在先前约定的现有 `P0_TEST_ROOT` 中创建 `.reeldock-p2-随机ID`，上传自建 NFO / JPG / `.actors` 并完整核对。验证客户端目标保护，同时用**相同哨兵内容**测服务器条件 PUT 能力，分别报告结果。它是资产上传验证，不冒充真实电影全链路。
- 缺配置退出 2，失败退出非零，成功退出 0；网络模式总时限 300 秒。仅输出安全状态 / 哈希，报告不提交。保留生成的远程测试目录供人工核对，不 DELETE、不 MOVE。

本地 UI fixture：

```bash
uv run python scripts/p2_demo.py --port 8022 --data-dir /tmp/reeldock-p2-demo
```

仅内存 Mock Provider，不读取 `.env.p0` / `.env.p2`；随机管理员凭证在指定目录 `ui-login.json`，可据此测试页面。fixture 的虚拟视频只有目录条目信息，没有可读取的视频内容。重建空 fixture 请使用新的专用本地目录；其内存远程数据不代表真实 WebDAV 持久性。

真实 NAS 完整电影包、Kodi 使用本次 P2 产物首次导入 / 刷新 / 真正断网、飞牛容器联调仍需独立验收。P0 的本机 Kodi 导入 / 刷新结果不会自动变成本阶段实测。
