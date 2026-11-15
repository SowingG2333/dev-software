# P5 / P6 / P7 实现说明

更新日期：2026-11-08。本文对应课程表格中的 **P5 加密、P6 自动化与增量、P7 网络**。P4（打包/压缩）不在本轮范围内：相关端点保留为 501，不提交 P4 代码。

## 0. 结论

| 阶段 | 状态 | 真实实现 |
| --- | --- | --- |
| P5 加密 | 已完成 | AES-256-GCM + PBKDF2-HMAC-SHA256；短期内存 `secretRef` |
| P6 自动化与增量 | 已完成 | 5 字段 cron、轮询去抖监听、硬链接增量、版本保留 |
| P7 网络 | 已完成（简单版） | 独立 HTTPS 远端服务、用户登录、按用户隔离、分块续传、SHA-256 校验、Range 下载 |

实现位于 `backnest/`，通过既有 `/api/v1` operationId 暴露；Web UI 的“扩展能力”页提供操作入口。

## 1. 运行模型

```text
Web UI / curl
  └─ /api/v1/<operationId>  (server.py：本机会话、Host/Origin 检查、任务线程)
      ├─ 基础备份/还原/校验：C++17 引擎（有 build/backnest.exe 时）
      │                        否则 backnest.engine 兼容引擎
      ├─ P5：backnest.crypto + secrets
      ├─ P6：backnest.scheduler / watcher / engine
      └─ P7：backnest.remote（客户端）
                 │ HTTPS
                 └─ backnest.remote_server（独立进程/端口，默认 127.0.0.1）
```

P7 远端服务**不是**本机 demo 桥：它单独监听 HTTPS 端口，必须登录，按用户隔离目录；本机 demo 桥只负责调用它。

## 2. P5 加密

### 2.1 算法

- KDF：PBKDF2-HMAC-SHA256（默认 200,000 次，16 字节随机盐，32 字节密钥）。
- AEAD：AES-256-GCM（12 字节 nonce，16 字节 tag）。
- Windows：系统 CNG（`bcrypt.dll`，ctypes）；macOS/Linux：可选 `cryptography`。
- 密码只换短期 `secretRef`（默认 600 秒），不写任务、清单、日志、URL 或 localStorage。

### 2.2 BNE1 格式

```text
32 字节头（作为 AEAD AAD）：magic BNE1, version=1, alg=1, kdf=1,
  iterations u32, saltLen=16, nonceLen=12, tagLen=16, plaintextLen u64,
  6 字节零填充
salt(16) || nonce(12) || ciphertext || tag(16)
```

错误密码、篡改、截断明确失败且不创建目标文件。

接口：`encryption.algorithms/encrypt/decrypt`、`secrets.create/revoke`。

## 3. P6 自动化与增量

### 3.1 增量

- `backup.start` 支持 `mode=full|incremental`。
- 与目标位置最新 `snapshot-*` 比较大小+CRC；未变文件硬链接，变更/新增复制；新版本仍写完整清单。
- 每个版本独立可校验/还原；删除旧基准不影响新版本。
- 暂存目录 `verify` 通过后才发布。

接口：`backup.start`、`incremental.preview`。

### 3.2 定时

- 5 字段 cron（分 时 日 月 周），支持列表/范围/步长；周日 0 或 7。
- IANA 时区；无 tzdata 的 Windows 支持 UTC/local/固定偏移和常见无夏令时区。
- `nextRunAt` 持久化，触发后推进；错过策略 `skip/run_once`。
- 与手动备份共享 `engine_lock`；忙时记录跳过，不重入；非法表达式停用计划。
- 只在 server 进程存活时运行，不是系统服务。

接口：`schedules.*`、`schedules.nextRuns`。

### 3.3 实时

- 轮询树签名（路径+大小+mtime+类型），默认 500 ms 去抖。
- 自动排除备份目标与 `excludedPaths`，避免“备份引发备份”。
- 一次变化只触发一次；备份期间的新变化下一轮检测。

接口：`watchers.start/get/stop`。

### 3.4 版本策略

- 默认保留全部；`settings.keepVersions=N` 只清理本后端登记、同一目标目录下最旧的 `snapshot-*`。

## 4. P7 网络

### 4.1 远端服务

独立进程 `scripts/remote_server.py`，默认 `127.0.0.1:4780`，HTTPS：

```sh
python scripts/remote_server.py \
  --state .backnest/remote --port 4780 \
  --add-user demo:demo-password
```

- 用户密码用 PBKDF2-HMAC-SHA256 加盐保存；登录返回 1 小时内存 token。
- 每个用户独立目录 `users/<username>/<snapshot-id>/`；跨用户读取返回 404。
- 快照由 `manifest.bkn` 与 `data/**` 组成，逐文件 SHA-256；上传按 256 KiB 分块。
- 上传支持 `X-Offset` 续传；偏移不匹配返回 409。
- 下载支持 `Range: bytes=...`，206 响应；客户端可断点续传。
- finalize 校验每个文件大小与 SHA-256，失败不标记完成。
- 首次启动在 `.backnest/tls/` 自动生成自签名测试证书（可用 `openssl` 或可选 `cryptography`）；该目录被 git 忽略，**仓库不保存私钥**。生产用 `--cert/--key` 指定真实证书和信任链。

### 4.2 主服务接口

| operationId | 说明 |
| --- | --- |
| `remotes.list/create/update/delete` | 远端配置；`credentialRef` 只存短期 secretRef，不持久化 |
| `remotes.test` | HTTPS 健康检查 + 登录，返回 connected/tlsVerified |
| `remotes.upload` | 本机 snapshot 目录 → 远端；`destination` 为远端 id，`resumeToken` 为远端快照 id |
| `remotes.download` | 远端快照 → 本机新目录；`snapshot` 为远端快照 id，`snapshotId` 为远端 id |
| `remoteCatalog.list/register/get/delete` | 远端版本元数据索引 |
| `users.list/create/update/delete` | 本机用户记录（密码用 secretRef 写入哈希） |
| `auth.login/logout` | 本机用户登录会话 |

`credentialRef` 的值是两行文本：

```text
远端用户名
远端密码
```

`remotes.create` 可带可选 `caFile`，用于私有/自签名 HTTPS 证书；测试时也可设置环境变量 `BACKNEST_REMOTE_CA`。

### 4.3 校验与隔离验收

- 正确用户名/密码可登录；错误密码 401。
- 用户 A 的 catalog 不包含用户 B 的快照；下载/删除他人快照返回 404。
- 上传中断后用 `resumeToken` 继续；下载用 Range 从已有偏移继续。
- 篡改任一分块，finalize 时 SHA-256 不一致并失败。
- 自签名证书必须提供 CA 文件；不提供时 TLS 验证失败。

## 5. UI

左侧“扩展能力”页：

- P7：创建远端、测试连接、上传本机备份版本、按远端快照 ID 下载、远端列表。
- P5：密码加密/解密；任务结束自动撤销 secretRef 并清空密码框。
- P6：增量预览、创建 cron 计划、开始/停止实时监听、查看后端计划。

## 6. 验证

```sh
python -m unittest discover -s tests -v
python scripts/demo_extensions.py
```

当前 Windows 结果：**85 项通过，3 项跳过**（C++ 符号链接测试、BNE1 符号链接测试、POSIX 权限测试）。P7 覆盖 TLS 验证、登录、用户隔离、上传/下载往返、续传、篡改检测、Range 下载和 HTTP API。

## 7. 已知限制

1. **P4 已移除**：`archive.*`、`compression.*` 仍存在但返回 501；不在本 PR 范围。
2. **P5/P6/P7 算法在 Python 层**；C++ 只负责基础备份/还原/校验。
3. 加密整文件读入内存，超大文件需要分块 AEAD。
4. 定时只在进程存活时执行；实时监听是轮询，不是系统文件通知。
5. P7 是简单远端：没有带宽限制、断点重试上限、配额、角色 ACL 管理界面；token 只在内存，服务重启后需重新登录。
6. P7 远端用户和主服务 `users.*` 是两套账户：远端用户通过 `scripts/remote_server.py --add-user` 创建；`users.*` 是本机账户记录。
7. 自签名测试证书在运行时生成到被忽略的 `.backnest/tls/`，仓库不保存私钥；仅供测试，不能用于生产。
8. macOS 本轮未重新实机验收；Windows 已用 Zig/Clang 构建 C++ 引擎并通过测试。
