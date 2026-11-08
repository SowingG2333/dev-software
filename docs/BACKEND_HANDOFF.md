# 后端开发交接文档

更新日期：2026-11-08。本文面向接手 C++ 引擎、Qt 桌面层或后端服务的开发者。本轮范围是 **P5 加密、P6 自动化与增量、P7 网络**；P4 打包/压缩不在本轮中。

## 1. 实现约束

| 范围 | 要求 | 当前落点 |
| --- | --- | --- |
| 目录备份/还原/校验 | 项目代码实现流程、清单、CRC32 | `core/main.cpp`、`backnest/engine.py` |
| 加密/解密/密码派生 | **允许成熟密码学库**，不要手写密码算法 | `backnest/crypto.py`：Windows CNG；macOS/Linux `cryptography` |
| 定时、增量、实时、远端传输 | 项目代码实现规则、调度、续传与校验 | `backnest/scheduler.py`、`watcher.py`、`remote.py`、`remote_server.py` |
| P4 打包/压缩 | 本轮不做 | `archive.*`、`compression.*` 保持 501 |

禁止把 demo 桥直接暴露到公网；P7 使用独立 HTTPS 服务。

## 2. 当前真实交付

### 已实现

- C++17 普通文件、子目录、空目录的完整备份、校验与还原；`BACKNEST 1` 清单 + CRC32。
- Python 兼容引擎：同一清单格式；无 C++ 二进制时回退。
- P5：AES-256-GCM + PBKDF2-HMAC-SHA256；短期内存 `secretRef`；错误密码/篡改失败。
- P6：5 字段 cron、轮询去抖监听、硬链接增量、可选版本保留、互斥与不重入。
- P7：独立 HTTPS 远端服务；PBKDF2 用户密码；token 登录；按用户目录隔离；分块上传/`X-Offset` 续传；逐文件 SHA-256；Range 下载；`remoteCatalog` 元数据。
- 任务/计划/设置/活动/版本索引/远端配置 JSON 仓储；重启保留配置。
- Web UI：基础功能 + “扩展能力”页（P5/P6/P7）。
- `/api/v1` 统一入口、鉴权、能力查询、OpenAPI、前端调用端口。

### 尚未实现

P4 打包/压缩、六维筛选、元数据/特殊文件保真、Qt 桌面、系统服务式定时、可靠取消、完整角色 ACL 管理界面。

## 3. 需求到接口

完整机器生成表见 [REQUIREMENTS_AUDIT.md](REQUIREMENTS_AUDIT.md)。

当前 **63 个 operationId：50 个接通、13 个预留**。以 `api_contract.py` 为唯一事实来源；改协议先改注册表再生成。

| 文件 | 责任 |
| --- | --- |
| `api_contract.py` | operationId、DTO、实现状态、能力、校验 |
| `api/openapi.json`、`api/backend-api.d.ts`、`backend-api.js` | 生成产物 |
| `core/main.cpp` | C++ 基础引擎 |
| `backnest/engine.py` | Python 兼容引擎 |
| `backnest/crypto.py` | BNE1 加密 |
| `backnest/scheduler.py` | cron/下次执行/错过策略 |
| `backnest/watcher.py` | 轮询去抖监听 |
| `backnest/remote.py` | P7 客户端、分块/续传 |
| `backnest/remote_server.py` | P7 HTTPS 服务、用户隔离 |
| `server.py` | 本机 HTTP 适配器、任务线程、v1 分发 |
| `scripts/remote_server.py` | 独立远端服务入口 |

## 4. 调用协议

1. `GET /api/session` 获取本机会话 token，并返回 `engine`（`C++17`/`Python`）。
2. 业务请求带 `X-BackNest-Token`，访问 `/api/v1/...`。
3. `GET /api/v1/capabilities` 查询实现状态、算法、自动化和远端能力。
4. `GET /api/v1/openapi.json` 返回契约。

本地桥只监听 `127.0.0.1`，检查 Host/Origin/`Sec-Fetch-Site`。P7 远端必须单独启动 HTTPS 服务。

### 4.1 已实现的调用

| 组 | operationId |
| --- | --- |
| 系统 | `system.capabilities/contract`、`filesystem.directories` |
| 基础 | `backup.start`、`restore.start`、`snapshots.verify`、`jobs.get` |
| P5 | `encryption.algorithms/encrypt/decrypt`、`secrets.create/revoke` |
| P6 | `incremental.preview`、`schedules.*`、`watchers.*`、`tasks.*`、`settings.*`、`snapshots.*`、`activity.*`、`workspace.overview` |
| P7 | `remotes.list/create/update/delete/test/upload/download`、`remoteCatalog.list/register/get/delete`、`users.list/create/update/delete`、`auth.login/logout` |

其余 13 个（P4、filters、metadata、fileTypes、jobs.cancel）返回 501。

### 4.2 异步与错误

- `202` 只表示任务已接收，客户端轮询 `jobs.get`。
- 终态 `completed`/`failed`；`cancelled` 枚举保留，取消未实现。
- 进度字段：`phase/files/totalFiles/bytes/totalBytes/path`。
- 400 参数错误；403 鉴权/来源；404 资源不存在；405 方法错误；501 预留/不支持；并发忙目前 400。
- 不静默忽略参数：`backup.start` 的 filters/metadata/archive/compression/encryption 选项、`restore.start` 的非空目标等仍返回 501。

### 4.3 P5 secretRef

1. `POST /secrets` `{value, purpose}` → `{secretRef, expiresAt}`（默认 600 秒）。
2. 加密/解密只传 `secretRef`，不传明文密码。
3. 任务结束/失败后 `DELETE /secrets/{id}`；不写入任务、清单、日志、URL、localStorage。

### 4.4 P6

- `backup.start` 支持 `mode=full|incremental`；硬链接未变文件，变更/新增复制，完整清单。
- cron 5 字段、IANA 时区、错过策略 skip/run_once；触发后推进 `nextRunAt`；与手动备份共享 `engine_lock`。
- watcher 轮询树签名 + 去抖，自动排除备份目标和 `excludedPaths`；一次变化只触发一次。
- `settings.keepVersions=N` 只清理本后端登记、同目标目录下最旧版本。

### 4.5 P7

启动远端：

```sh
python scripts/remote_server.py --state .backnest/remote --port 4780 \
  --add-user demo:demo-password
```

- 远端用户密码 PBKDF2 加盐；登录 token 1 小时，内存保存。
- 每用户目录 `users/<username>/<snapshot-id>/`；跨用户 404。
- 创建快照 `POST /snapshots`，上传 `PUT /snapshots/{id}/files?path=...` + `X-Offset`，`finalize` 校验 SHA-256，下载 `Range`。
- 主服务 `remotes.create` 的 `credentialRef` 值是两行文本：远端用户名、远端密码；`caFile` 可选。
- `remotes.upload`：`snapshot` 本机版本目录，`destination` 远端 id，`resumeToken` 远端快照 id。
- `remotes.download`：`snapshot` 远端快照 id，`snapshotId` 远端 id，`destination` 本机新目录。
- 首次启动自动在 `.backnest/tls/` 生成自签名测试证书；私钥不提交到仓库，生产用 `--cert/--key` 指定真实证书。

## 5. 数据格式

- 目录型版本：`snapshot-*/manifest.bkn` + `data/`，两种引擎共用。
- BNE1：32 字节头（AAD）+ salt/nonce/密文/tag；PBKDF2 + AES-256-GCM。
- 远端快照：`manifest.bkn` + `data/**`，逐文件 SHA-256；上传分块 256 KiB，Range 下载。
- 所有新格式保留版本标识与旧读取器；不要直接改变 v1 解释。

## 6. 平台与限制

- Windows 已实测 C++17 引擎（Zig/Clang 构建）、Python 引擎、P5–P7。
- macOS C++ 基础引擎沿用原实现；P5–P7 本轮未复测。
- 加密整文件读入内存；超大文件需分块 AEAD。
- 定时只在进程存活时执行；实时监听是轮询。
- P7 简单远端：无配额、带宽限制、角色 ACL 界面；token 内存保存，重启后重新登录。
- 远端用户与主服务 `users.*` 是两套账户；远端用户用 `--add-user` 创建。
- 不支持符号链接、特殊文件、完整元数据、六维筛选、Qt 桌面。
- 还原只接受新/空目录；job 重启不可恢复。

## 7. 开发与验收

```sh
python server.py
python -m unittest discover -s tests -v
python scripts/generate_api_contract.py
python scripts/demo.py
python scripts/demo_extensions.py
```

当前 Windows：85 项通过，3 项跳过（符号链接/权限）。P7 覆盖 TLS、登录、用户隔离、上传/下载、续传、篡改检测、Range。测试对照库（Node.js AES-GCM、zlib/hashlib）不进入产品路径。

修改接口顺序：`api_contract.py` → 生成产物 → handler/算法 → 测试 → 更新能力/需求表。不要先改 `implemented` 点亮按钮。

## 8. 接手优先顺序

1. 在目标平台完成基础往返，确认 C++/Python 引擎选择。
2. 补 P4 打包/压缩（如课程要求），保持 P5–P7 接口稳定。
3. 补可靠取消、持久化 job 状态、Qt 桌面与系统服务式定时。
4. 增强 P7：真实证书部署、角色 ACL、配额/带宽、token 持久化或 OAuth。
5. 再推进六维筛选、元数据与安装包。
