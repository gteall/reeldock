# ReelDock · 影坞：开发进度与交接

更新日期：2026-10-08。

## 当前状态

P0 验证工具已实施并完成离线测试，以及部分真实 WebDAV / 射手 / Kodi 联调。**完整 P0 仍未验收完成**：Kodi 真正断网、大型实际片源、真实 NAS 故障注入及版本信息待补齐。尚无产品应用或镜像，P1–P5 未开始。

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

## 阶段状态

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| P0 外部能力验证 | 工具完成，部分实机验收待补 | 离线回归及真实 WebDAV / 射手 / Kodi 导入刷新已通过；断网、大型片源和真实故障待验 |
| P1 工程基础 | 未开始 | 尚无前后端骨架和任务数据库 |
| P2 电影基础刮削 | 未开始 | 尚无正式 TMDB / WebDAV 刮削闭环 |
| P3 字幕与归档 | 未开始 | 规则已设计，尚无实现 |
| P4 电视剧与完整 UI | 未开始 | 尚无实现 |
| P5 NAS 发布 | 未开始 | 尚无可运行镜像 / 实机验收 |

## 最新业务约束

- 整包基础 NFO / 图片 / 演员必需资产先全部远程验证，才能进入字幕阶段。
- TMDB original_language 默认中文集合 zh / cn，命中后 skipped_tmdb_chinese，零视频探测、零射手哈希和请求。
- TMDB 非中文才探测实际音轨 / 字幕；已有有效外挂不限语言。
- 基础 NFO 无 FFprobe 依赖，未探测 streamdetails 省略。
- 最终 manifest 和安全移动仍需通过，不因中文跳过字幕而跳过归档校验。

## 下一步

补充精确 OpenList 版本 / 存储驱动；在有隔离条件时完成 Kodi 真正断网且新 ID、无缓存的首次导入 / 刷新。可选提供代表性大型 MKV / MP4 及相同本地副本，验证远端 seek / 指纹成本。真实故障注入需要专用环境，不在正常 NAS 上主动断连接。P0 本次不需要 TMDB Key，带凭证的 TMDB 搜索 / 图片闭环留在 P2。

后续进入 P1 时复用 P0 的协议结论和故障用例，按既定 Python / FastAPI / HTTPX、SQLite、React 技术栈建立产品骨架，不能把 P0 标准库探测器当作已实现的生产 Worker。飞牛 Docker 在 P5 实施；当前按用户选择直接在本机开发和联调。

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

## 后续阶段交接格式

每次实施阶段后更新上面的状态，并追加：实现内容、相关提交 / 分支、实际运行的命令和结果、Mock 与真实验证边界、剩余错误 / 未验证事项、下一步入口。不能只凭代码生成或 Mock 通过标记实机验收完成。
