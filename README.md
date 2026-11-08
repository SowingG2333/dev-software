# 归档 BackNest：C++ / Python 备份与扩展能力 demo

一个可真实运行的本地备份工作台：目录树备份与还原、认证加密、定时/实时/增量备份、HTTPS 远端备份，均通过 `/api/v1` 统一接口执行。

- **基础闭环**：C++17 引擎负责扫描、复制、清单和 CRC32 校验；Web UI 通过仅监听 `127.0.0.1` 的 Python 通信桥调用。无 C++ 二进制时自动使用兼容的 Python 引擎，仍然执行真实文件操作。
- **P5 加密**：AES-256-GCM + PBKDF2-HMAC-SHA256；密码只换短期内存 `secretRef`。
- **P6 自动化**：5 字段 cron、轮询去抖实时监听、硬链接增量、可选版本保留、互斥与不重入。
- **P7 网络**：独立 HTTPS 远端服务，用户登录、按用户隔离、分块续传、SHA-256 校验、Range 下载。

> 本轮范围是 **P5/P6/P7**。P4（自实现打包/压缩）不在本 PR 中：`archive.*`、`compression.*` 仍保留为 501。

## 当前状态

| 范围 | 状态 |
| --- | --- |
| 基础备份/还原/校验 | 已实现；C++17 引擎与 Python 兼容引擎共用 `BACKNEST 1` 清单 |
| 接口契约 | 63 个 operationId，P5/P6/P7 相关端口已接通；P4、筛选、元数据等仍明确返回 501 |
| P5 加密 | 已实现并测试：AES-256-GCM、PBKDF2、secretRef |
| P6 定时/实时/增量 | 已实现并测试：cron、去抖轮询、硬链接增量、版本保留 |
| P7 网络 | 已实现并测试：HTTPS、用户隔离、上传/下载续传、SHA-256、Range |
| 平台 | Windows 已实测 C++17 引擎（Zig/Clang 构建）、Python 引擎和 P5–P7；macOS 基础引擎沿用原实现，本轮未复测 |
| 未实现 | P4 打包/压缩、六维筛选、元数据保真、特殊文件、Qt 桌面、系统服务式后台 |

详细格式、接口和验收见 [P5/P6/P7 实现说明](docs/FEATURES_P5_P7.md)。

## 启动界面

需要 Python 3.10+。有 C++ 二进制时优先使用；没有时自动回退到 Python 兼容引擎。

```sh
python server.py
```

打开 <http://127.0.0.1:4173/>。右上角显示“本机引擎 · C++17”或“本机引擎 · Python”。

```sh
python server.py --port 4175
python server.py --state-dir .backnest/my-state
```

## 使用流程

### 基础备份与还原

1. **新建备份**：填写名称，选择源目录和备份位置。
2. **执行备份**：创建任务本身不复制文件；点击“运行”后进度来自实际文件操作。
3. **查看版本**：每次成功备份生成独立 `snapshot-*` 目录。
4. **数据还原**：选择版本，填写新目录或空目录；先校验再写入。
5. **导入备份**：界面记录清空后，可校验并导入已有 `snapshot-*`。

### 扩展能力页（P5/P6/P7）

- **P7 网络**：填写远端 HTTPS 地址、用户名、密码、可选 CA 文件，创建远端；测试连接；上传本机 `snapshot-*`；按远端快照 ID 下载到新目录。
- **P5 加密**：输入密码加密/解密；任务结束自动撤销 secretRef 并清空密码框。
- **P6 自动化**：增量预览、创建 cron 计划、开始/停止实时监听、查看后端计划。

## P7 远端服务

P7 需要单独启动一个 HTTPS 远端（不是本机 demo 桥）：

```sh
python scripts/remote_server.py \
  --state .backnest/remote --port 4780 \
  --add-user demo:demo-password
```

- 默认只监听 `127.0.0.1`；生产部署需用 `--cert/--key` 指定真实证书和信任链。
- 首次启动会在 `.backnest/tls/` 自动生成自签名测试证书；该目录被 git 忽略，**仓库不保存私钥**。
- 主服务创建远端时，`credentialRef` 的内容是两行文本：
  ```text
  远端用户名
  远端密码
  ```
- 自签名证书测试时，`remotes.create` 的 `caFile` 填 `.backnest/tls/ca.pem`，或设置环境变量 `BACKNEST_REMOTE_CA`。

远端协议：登录 token、按用户目录隔离、`POST /snapshots` 创建、`PUT .../files?path=...` 分块上传、`X-Offset` 续传、`finalize` 校验 SHA-256、`Range` 下载。

## 数据格式

### 目录型版本（两种引擎共用）

```text
备份位置/
└── snapshot-<唯一标识>/
    ├── manifest.bkn    # BACKNEST 1：源目录、文件大小、CRC32、UTF-8 相对路径
    └── data/           # 原始文件和目录层级，包括空目录
```

- 64 KiB 流式复制；先写 `.partial-*`，校验后重命名发布。
- 还原只接受新目录或空目录，不覆盖已有文件。
- 增量版本用硬链接复用未变数据，但仍有完整清单，可独立还原。

### 加密文件（BNE1 v1）

32 字节头（作为 AEAD AAD）+ salt、nonce、密文、tag；PBKDF2-HMAC-SHA256 + AES-256-GCM。

### 远端传输

- 逐文件 256 KiB 分块，`X-Offset` 续传；
- 每文件 SHA-256，finalize 时校验；
- 下载 `Range: bytes=...`，206 部分内容；
- 用户目录隔离，跨用户读取返回 404。

## 命令行

```sh
# 有 C++ 编译器时构建原生引擎
python scripts/build.py

# C++ 基础命令
build/backnest backup "/绝对路径/源目录" "/绝对路径/备份位置"
build/backnest verify "/绝对路径/备份位置/snapshot-实际版本标识"
build/backnest restore "/绝对路径/备份位置/snapshot-实际版本标识" "/绝对路径/新的还原目录"

# 基础样例
python scripts/demo.py

# P5/P6/P7 完整 HTTP 演示
python scripts/demo_extensions.py
```

Windows 上可用 Visual Studio C++ 工具，或用 Zig/Clang 的 `zig c++` 构建：

```sh
zig c++ -std=c++17 -O2 -municode core/main.cpp -o build/backnest.exe
```

本机已用 Zig 0.17.1（PyPI wheel，工具链放在工作区 `.tools/`，不入库）构建 `build/backnest.exe`，并通过 12 项 C++ 引擎测试。`build/` 被 git 忽略。

## 验证

```sh
python -m unittest discover -s tests -v
python scripts/demo_extensions.py
```

当前 Windows 结果：**85 项通过，3 项跳过**（符号链接权限、POSIX 权限）。覆盖基础 API/契约、C++ 引擎、BNE1、增量、cron、watcher、secretRef、版本保留、P7 TLS/用户隔离/续传/篡改检测和 HTTP 端到端。加密用 Node.js AES-GCM 独立向量做交叉验证（仅测试参考）。

## 后端交接与接口

- [P5/P6/P7 实现说明](docs/FEATURES_P5_P7.md)
- [后端交接文档](docs/BACKEND_HANDOFF.md)
- [全量需求核验表](docs/REQUIREMENTS_AUDIT.md)
- [OpenAPI 契约](api/openapi.json)、[前端类型](api/backend-api.d.ts)
- `api_contract.py` 是唯一接口注册表；`python scripts/generate_api_contract.py` 生成 OpenAPI/JS/类型/需求表。

## 已知限制

- P4 已移除，`archive.*`/`compression.*` 仍为 501。
- 加密整文件读入内存；超大文件需分块 AEAD。
- 定时只在进程存活时执行；实时监听是轮询。
- P7 是简单远端：无带宽限制、配额、角色 ACL 管理界面；token 仅在内存；重启后需重新登录。
- 远端用户和主服务 `users.*` 是两套账户；远端用户用 `scripts/remote_server.py --add-user` 创建。
- 自签名证书/私钥仅用于测试，不能用于生产。
- 不支持符号链接、特殊文件、完整元数据保真、六维筛选、Qt 桌面。
- macOS 本轮未复测。

## 项目文件

| 文件 | 职责 |
| --- | --- |
| `core/main.cpp` | C++17 备份/还原/校验引擎 |
| `backnest/engine.py` | 兼容 Python 引擎 |
| `backnest/crypto.py` | AES-256-GCM + PBKDF2 |
| `backnest/remote.py` | P7 HTTPS 客户端、分块/续传 |
| `backnest/remote_server.py` | P7 HTTPS 远端服务、用户隔离 |
| `backnest/scheduler.py` | cron、下次执行、错过策略 |
| `backnest/watcher.py` | 轮询去抖监听 |
| `backnest/secrets.py` | 短期 secretRef |
| `backnest/store.py` | 任务/计划/设置/日志/远端索引/本机用户 |
| `server.py` | 本地 HTTP 桥、P5/P6/P7 分发 |
| `scripts/remote_server.py` | 独立远端服务启动入口 |
| `scripts/demo_extensions.py` | P5/P6/P7 HTTP 演示 |
| `tests/` | 基础/扩展/P7/契约/HTTP 测试 |
| `docs/FEATURES_P5_P7.md` | 本轮实现、格式、验收与限制 |
