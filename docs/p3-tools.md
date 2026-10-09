# P3 字幕与安全归档

P3 仅覆盖 P2 已支持的独立电影包。电视剧留待 P4。启动方式见 [development.md](development.md)，使用当前锁文件执行 `uv sync --frozen`；本机需安装 FFprobe / FFmpeg，开发镜像已包含两者。

## 使用

1. 设置页保存连接、TMDB 和探测预算。至少两次扫描，通过稳定窗口后选择电影的“刮削并按策略归档”。旧 API 的 `movie_base` 仍仅执行 P2；界面提交 `package_pipeline`，按完整五步执行。
2. 基础 NFO、海报、背景及所选必需头像全部远程读回通过后，再次检查字节哈希才进入字幕分流。失败时零探测、零视频 Range、零射手调用。
3. `original_language` 命中配置的 `zh/cn` 则 `skipped_tmdb_chinese`，实际音轨显示未探测；不检查现有字幕。缺失 / 未识别语言待确认。
4. 明确非中文才探测默认音轨；明确默认中文、完整内置简体文本字幕或匹配且有效的已有外挂满足其一即可免下载。默认轨冲突 / 语言未知待确认。已有外挂不限语言，需与视频 stem 匹配，支持同目录、`subs/`、`subtitles/`；SRT / ASS / SSA 解析时间轴，IDX/SUB 必须成对、检查索引及 MPEG 包并经本地 FFprobe demux。图片字幕不做 OCR。
5. 不满足时读取射手协议的四块内容哈希，查询并验证候选，按简体 / 双语描述优先。下载经过编码识别、结构 / 时间轴校验，处理正负延时并规范为 UTF-8；负延时裁去零时刻前的片头 cue，保留原始下载于本地内容缓存。文件采用 `<video>.shooter.<hash>.srt/ass`，不将未知语言标为简体。
6. 没有命中或字幕失败保持源目录，可人工放入匹配字幕后重试原任务。重试复用同版本基础资产和实际探测证据，不重新抓取全部元数据。

“完整内置简体”要求明确 Hans / 简体标签、非 forced / 解说轨、文本可抽取解析、包含汉字且 OpenCC t2s 未发现繁体差异，覆盖片长至少一半、开始不晚于 20%；长片至少 20 个有效 cue。纯 `chi` 标签、繁体、未知图片字幕不能直接豁免。此保守启发式和时间轴粗校验不能证明每句对白同步；无法在预算内证明时阻塞，用户可补外挂。

## 归档信任边界

自动归档默认关闭。设置页的“已验证 MOVE”是管理员对**当前 WebDAV 地址、凭证和输入 / 输出路径所对应存储驱动范围**的显式确认，并非 OPTIONS 响应或同主机名推断。先在专用目录用 P0 / P3 工具验证同驱动移动及冲突保护，再确认；更换地址、凭证或路径后自动撤销。只在测试根目录验证，不能证明其它挂载或跨驱动路径安全。

最终清单为不可变的 `.reeldock/manifest-v<version>-<hash>.json`，记录包 ID、原始语言、版本、字幕证据、所有普通附件的目录快照及必需小资产哈希。版本与内容寻址避免覆盖人工文件和旧清单；自身的登记清单排除于源附件集合，避免循环哈希，`.reeldock` 中的其它用户附件仍纳入核对。

移动前复核清单、全包文件信息快照、源媒体与目标冲突，提交持久化意图，发送 `Overwrite:F`。移动后只读核对两侧、目标包身份 / 清单、所有附件以及必需小文件完整哈希；成功才更新数据库路径。没有整视频复制或删除接口。

- 目标存在：阻塞，不覆盖 / 合并。
- 207 子项失败：保存失败相对路径和数量，标记 `partial_failure`；不自动重发或删源。
- 超时 / 崩溃后源不存在、目标清单及资产正确：修复为已归档，不再 MOVE。
- 源完整、目标不存在：仅在前次请求明确拒绝且核对完整时允许重试；超时不能证明服务器停止，标记 `move_unknown`。
- 两侧都有、两侧都没、缺失项或目标身份不符：保留现状、人工核对。重试按钮首先核对，不是强制搬运。
- 有未解决移动意图时，扫描不改变历史上下文，新任务 / 手动改 ID 被拒绝；请恢复原任务。修改配置版本会阻塞原任务，恢复原范围及数据库备份应人工审查，不能用新任务绕过。

当前 OpenList 忽略条件 PUT，stat + 租约不等于外部程序间的原子互斥。处理与归档期间媒体包必须独占管理。视频只核对相对路径、大小及可用 ETag / mtime，不做全文哈希；视频或普通附件版本信息改变时保守停止核对；必需小资产仅在全文哈希一致时允许 MOVE 改变 ETag / mtime。完整边界见 [compatibility.md](compatibility.md)。

## 可重复验证

```sh
uv run python scripts/p3_verify.py mock --output reports/p3-mock.json
uv run python scripts/p3_verify.py shooter --output reports/p3-shooter.json
uv run python scripts/p3_verify.py webdav --output reports/p3-webdav.json
```

`mock` 包含故障矩阵及本机自建两秒媒体的真实回环网关 / FFmpeg 测试；没有 FFmpeg 时该实际进程测试跳过并明确未验证。`shooter` 只使用 P0 公开指纹，证明候选下载与格式解析，不证明匹配当前电影或同步。

`webdav` 只读本地忽略的 `.env.p0`，要求现有 `P0_TEST_ROOT`；只在其中新建 `.reeldock-p3-<UUID>/input` 和 `output`，上传自建小样本及 Mock TMDB 基础资产，走真实 WebDAV、manifest、MOVE。不会使用 `P0_SCAN_PATH`，不会写 / 移动正式电影包，不读取视频内容。随机测试目录、资产与本地 `reports/p3-runs/<ID>` 的意图数据库 / 密钥保留；不执行远端删除。报告、密钥和数据库不得提交公开仓库。

界面隔离样例：

```sh
uv run python scripts/p3_demo.py --data-dir /tmp/reeldock-p3-demo-new --port 8023
```

目录必须全新；随机本机测试账户保存在该目录 `ui-login.json`。只使用内存 Mock，中文和英语两个包用于界面验证，不导入 `.env.p0/.env.p2`。

协议依据：[FFprobe JSON / streams](https://ffmpeg.org/ffprobe.html)、[FFmpeg stream selection](https://ffmpeg.org/ffmpeg.html)、[WebDAV MOVE / Overwrite](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9)。射手是历史协议，服务可用性和 NAS 网络需要分别实测。
