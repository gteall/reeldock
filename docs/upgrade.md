# 备份、恢复、迁移与回退

适用于 P5 维护命令；不自动更新镜像、发布版本或替换已有状态。SQLite、密钥必须位于 NAS 本地磁盘，应用和维护命令使用相同 UID/GID。

## 备份

运行中可执行：

```bash
docker compose exec reeldock reeldock backup \
  --output /data/backups/before-upgrade.tar.gz
```

备份使用 SQLite online backup API，包含已提交的 WAL 内容，不直接复制活跃主文件。归档包含 `reeldock.sqlite3`、存在时的 `master.key`、格式/应用版本/schema/各文件 SHA-256 清单。完整校验后以 0600 原子创建，已有目标不覆盖；应为每次备份选择新文件名。

**备份包含解密凭证所需的密钥、管理员信息与会话，应按凭证保护并另存一份。** SHA-256 检测损坏，不替代备份来源可信度或加密存储。缓存不纳入备份，可从远端已验证的小资产或 Provider 重新获取。备份也不包含远端媒体或已归档产物，不能撤销 WebDAV 上已经发生的 MOVE。

升级前建议停应用后再备份，以减少远端操作与历史数据库快照的时间差：

```bash
docker compose stop reeldock
docker compose run --rm --no-deps reeldock backup \
  --output /data/backups/offline-before-upgrade.tar.gz
```

本机源码同样支持 `uv run reeldock backup --output reports/before-upgrade.tar.gz`，默认读取本地 `data/`；用 `REELDOCK_DATA_DIR` 指定其它本地目录。不要把备份放进公开仓库。

## 升级

1. 记录旧镜像 ID、Compose / `.env`、当前版本及已有待恢复 MOVE 意图。保留旧镜像。
2. 停止应用并完成备份，复制备份到另一个本地/离线位置。
3. 取得已验证的候选镜像或构建所审阅的源码。发布镜像应固定版本标签或 digest，不依赖 latest。
4. 在停机状态下迁移，再启动：

```bash
docker compose run --rm --no-deps reeldock migrate
docker compose up -d --no-build
docker compose ps
```

应用启动也会幂等迁移。启动前检查数据库完整性、已知 schema 和加密配置/密钥配对；密钥缺失或不匹配、未来未知 schema 会在迁移前拒绝，不新建替代密钥。不删除 `master.key` 解决登录/解密问题。

`migrate` 与服务共享本地实例锁，服务仍运行时会拒绝迁移。管理员初始化是独立短事务，可以在健康运行的同版本服务中执行。

## 恢复到新目录

先停止应用。准备由应用 UID/GID 拥有的**空本地目录**，不要覆盖旧目录；示例路径均需替换为实际位置：

```bash
# MAINTENANCE_IMAGE 使用包含 P5 restore 命令、且认识备份 schema 的镜像。
docker run --rm --user 10001:10001 \
  -v /nas-local/backups:/snapshots:ro \
  -v /nas-local/reeldock-restored:/data \
  reeldock:0.1.0-local restore --input /snapshots/before-upgrade.tar.gz
```

恢复拒绝运行中的目录、非空目录、未知 schema、损坏校验和、缺失/不匹配密钥、重复成员、符号链接及路径穿越。全部校验后才写入数据库与密钥；缓存目录可保持空。当前归档支持单数据库不超过 4 GiB。

修改 `.env` 的 `REELDOCK_DATA_PATH` 指向新目录，先用 `REELDOCK_WORKER_ENABLED=false` 启动查看状态。核对远端源/目标和待恢复意图后，再启用 Worker。旧备份可能早于某次远端移动；程序仍使用现有目标冲突、源快照和意图核对，不把“恢复数据库”当成远程回滚。

## 升级失败回退

停止新版本。保留失败后的目录用于核对；将**升级前备份**恢复到另一空目录，并切回保留的旧镜像 ID。不要仅启动旧镜像去读取已升级 schema，也不要自动执行破坏性 downgrade。

若旧镜像来自 P1–P4，不含 `restore` 命令，用 P5 维护镜像/源码恢复旧备份，再以旧镜像运行；旧 schema 必须与所用旧镜像匹配。P4 的本地缓存默认在 `/data/cache`，可重建；P5 生产镜像使用独立 `/cache`。

本次自动化包含旧 schema 备份→升级→模拟不可兼容升级→在新目录恢复原 schema，以及损坏包、错误密钥、服务持锁与配置/任务保留。实际命令和结果见 [进度](progress.md)；不把自动化替代真实 NAS 的断电/磁盘故障验收。

依据：[Python 3.12 SQLite backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup)。
