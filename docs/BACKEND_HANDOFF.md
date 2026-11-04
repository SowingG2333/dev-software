# 后端开发交接文档

更新日期：2026-11-04。本文面向接手 C++ 引擎、Qt 桌面层或后端服务的开发者。

## 1. 首先阅读：算法实现约束

**用户明确要求：“压缩解压等算法不要调用库（加密解密可以调用），要自己实现。”**

这是实现与验收约束，不是可选优化。后端开发必须遵守：

| 范围 | 实现要求 |
| --- | --- |
| 打包、解包 | 自行设计/实现容器组织、头部、目录/条目索引、长度编码及读取逻辑，不直接调用现成打包/解包库或系统程序完成作业功能 |
| 压缩、解压 | 自行实现编码和解码算法、必要的数据结构和格式；不可调用现成压缩/解压 API，也不能用子进程调用工具替代算法 |
| 目录备份/还原、筛选、增量比较、校验等核心逻辑 | 由项目代码实现流程、规则和数据结构。现有 C++ 引擎自行流式复制与计算 CRC32 |
| 加密、解密、密码派生及认证 | **允许调用成熟密码学库**。不要为满足“自己实现”而手写不安全的密码算法 |
| 文件系统、线程、时间、窗口、JSON、HTTP、数据库 | 可使用标准库、系统 API、Qt 或普通基础设施库；这些不等于调用现成压缩算法 |
| 测试对照 | 测试中可用独立实现核验结果；禁止把对照库引入产品压缩/打包执行路径。目前 `zlib.crc32` 仅在测试中对照 CRC32，`hashlib` 仅用于测试/样例的独立 SHA-256 比较 |

明确禁止的捷径包括：用 zlib、libarchive、miniz、现成 ZIP/TAR 压缩库直接实现本项目的压缩/打包功能；或调用 `zip`、`unzip`、`tar`、`gzip`、`7z` 等系统工具并声称算法已经自行实现。换个包装类或子进程接口不算自行实现。

建议先完成一种可说明、可测试的自实现算法。例如先做分块 Huffman 编码/解码和一个有版本号的自定义容器，再考虑第二种算法。此例只是实现建议，不是已实现功能或已确定的评分承诺。算法列表只能公布实际完成并验收通过的算法。

## 2. 当前真实交付范围

### 已实现

- C++17 普通文件、子目录、空目录的完整备份与还原。
- 独立 `snapshot-*` 版本，包含 `manifest.bkn` 与 `data/`，不依赖浏览器数据库才能还原。
- 文件大小与自行实现的 CRC32 校验；损坏、缺失、额外条目和不安全清单被拒绝。
- 先写临时目录、成功后发布版本；还原仅接受新目录或空目录，拒绝覆盖现有文件。
- Web UI 创建任务、选目录、真实运行进度、版本还原、校验导入、日志和偏好设置。
- 初始状态为空，不放入虚构任务、文件数量、大小、完成记录或版本。
- 旧 UI 中的示例数据会迁移清理，用户任务配置保留。
- `/api/v1` 统一入口、前端调用端口、C++ 扩展接口声明、能力查询和 OpenAPI 文档。

### 尚未实现

特殊文件保真、元数据恢复、自定义六维筛选、单文件打包、压缩、加密、定时、实时监听、增量、网络备份、远端账户、服务端持久化、取消运行、Qt 桌面窗口和双平台发布。

目录型 `manifest + data` 备份不是课件的“拼接为一个大文件保存”，**不能据此宣称打包解包扩展已完成**。浏览器内日志不等于服务端审计日志。当前本机令牌也不等于网络多用户登录。

macOS Apple Silicon 已实测。Windows 仅有构建入口和 Unicode 参数处理，尚未实机验收。应用在两个系统各自运行，与跨系统恢复所有权限/属主，是不同的兼容范围，分别验收。

## 3. 需求到接口的全量索引

完整机器生成表见 [REQUIREMENTS_AUDIT.md](REQUIREMENTS_AUDIT.md)，覆盖课件基础/扩展的 13 个条目（基础的备份、还原分开计行）。

当前共有 **63 个 operationId：7 个已接通、56 个预留**。以 `api_contract.py` 为唯一事实来源，不能手改生成文件后不更新源注册表。

| 文件 | 责任 |
| --- | --- |
| `api_contract.py` | operationId、HTTP 方法/路径、实现状态、请求/响应 DTO、需求映射、能力信息、请求校验和未实现选项保护 |
| `api/openapi.json` | 生成的 OpenAPI 3.1 契约，供对接与接口工具导入 |
| `api/backend-api.d.ts` | 生成的 TypeScript DTO 和调用端类型声明 |
| `backend-api.js` | 生成的统一前端 `BackNestBackend.invoke()`，所有声明操作均有调用入口 |
| `core/backend_interfaces.hpp` | C++ 备份、特殊类型、元数据、筛选、打包、压缩、加密、定时、监听、远端、用户、目录索引和工作区接口 |
| `workspace-store.js` | 浏览器持久化默认适配器，以及 `load()/save()` 后端存储替换端口 |
| `native.js` | 页面到后端端口的实际连接；不直接拼接业务接口 URL |
| `server.py` | 目前的本地 HTTP 适配器、鉴权、任务线程及 C++ CLI 子进程；预留接口在这里统一返回 501 |
| `core/main.cpp` | 当前真实基础引擎；包含接口头以确保声明参与编译，但**尚未将 CLI 逻辑重构为 `IBackupEngine` 实例** |

C++ 抽象接口是扩展边界，不是空实现。不要给默认实现返回 `true` 或假结果来绕过编译；接入时需要实现具体适配器并保持既有 CLI 回归测试。

## 4. 调用协议与兼容约定

### 4.1 初始化与鉴权

1. 本机 UI 调用 `GET /api/session`，获取短期本机会话令牌。此接口用于启动握手，不属于远端账户登录。
2. 业务请求统一带 `X-BackNest-Token`，访问 `/api/v1/...`。
3. `GET /api/v1/capabilities` 查询 operationId 的 `implemented`、算法列表、平台状态、存储状态和算法实现约束。
4. `GET /api/v1/openapi.json` 返回当前契约。

本地通信桥只监听 `127.0.0.1`，检查 Host 和 Origin，不允许跨站调用。它是 demo 适配器，不是可直接部署到公网的网络备份服务。

旧 `/api/backup`、`/api/restore`、`/api/verify`、`/api/jobs/{id}` 和 `/api/directories` 保留供旧客户端兼容。当前界面已切换到 v1，新增功能不要继续添加无版本路径。

### 4.2 已实现的 7 个调用

| operationId | HTTP | 结果 |
| --- | --- | --- |
| `system.capabilities` | `GET /api/v1/capabilities` | 真实能力清单 |
| `system.contract` | `GET /api/v1/openapi.json` | 接口契约 |
| `filesystem.directories` | `GET /api/v1/filesystem/directories?path=...` | 本机目录列表 |
| `backup.start` | `POST /api/v1/backups` | `202 {"id":"job-id"}` |
| `restore.start` | `POST /api/v1/restores` | `202 {"id":"job-id"}` |
| `snapshots.verify` | `POST /api/v1/snapshots/verify` | `202 {"id":"job-id"}`，供校验/GUI 导入 |
| `jobs.get` | `GET /api/v1/jobs/{jobId}` | 当前进度、终态与真实结果 |

```json
{
  "source": "/absolute/source",
  "destination": "/absolute/backups",
  "options": {"mode": "full"}
}
```

```json
{
  "snapshot": "/absolute/backups/snapshot-actual-id",
  "destination": "/absolute/new-or-empty-directory",
  "conflictPolicy": "require_empty"
}
```

接口接受的是本机路径，不是浏览器上传文件内容。JSON 中统一表达 Unicode，Windows 桥接时需要正确转换 UTF-8/UTF-16，不以本机用户名或固定盘符拼接路径。

### 4.3 异步结果与错误

- `202` 只表示任务已接收，不能立即添加成功版本。
- `jobs.get` 的终态是 `completed` 或 `failed`；`cancelled` 已保留枚举，但取消能力目前没有实现。
- 当前进度字段为 `phase / files / totalFiles / bytes / totalBytes / path`。`path` 是当前处理的相对路径；成功 `result.path` 是实际产物路径。
- 当前阶段包含 `starting / scanning / copying / verifying / completed`，扫描期未知总量不能伪造完成百分比。
- 文件操作失败的 job 查询可以返回 HTTP 200，但其中 `state=failed`，UI 必须按失败处理。
- 接口参数错误返回 400；鉴权/来源错误返回 403；未知资源 404；已知路径错误方法 405；预留或不支持的功能 501。原型并发忙碌目前也用 400；若未来改为 409，应同步契约和客户端。

```json
{
  "error": "接口已预留，后端功能尚未实现。",
  "code": "NOT_IMPLEMENTED",
  "operationId": "compression.compress",
  "capability": "compression.compress"
}
```

**禁止默默忽略参数。** 当前若 `backup.start` 要求筛选、保真链接、元数据、增量、打包、压缩、加密，或还原要求覆盖已有文件，适配器会返回 501，不执行一个与请求不一致的普通备份。

### 4.4 前端和 Qt 的统一端口

```js
const api = new BackNestBackend({ token });
const accepted = await api.invoke('backup.start', {
  body: { source, destination, options: { mode: 'full' } }
});
const job = await api.invoke('jobs.get', { path: { jobId: accepted.id } });
```

预留能力使用相同入口，例如 `filters.preview`、`archive.pack`、`compression.decompress`、`metadata.apply`、`schedules.create`。完整参数和返回类型在 `api/backend-api.d.ts`。

Qt WebChannel 可以通过构造参数 `transport(operationId, args)` 替换 HTTP transport。此时 Qt 必须继续按 operationId 做显式分发、参数与路径校验，并返回同样 DTO；不能让网页传任意命令或 shell 字符串。

### 4.5 任务、设置、日志与版本索引

当前来源：浏览器 localStorage；当前后端没有持久化数据库。以下两组接口都已预留：

- 细粒度接口：`tasks.* / settings.* / activity.* / snapshots.* / workspace.overview`。
- UI 工作区端口：`workspaceStore.attach({load, save})`，供后端聚合上述接口。默认浏览器适配器仍工作，保存采用独立快照，后端异步保存失败时不清除本机副本。

**没有自动启用后端持久化。** 接手者应实现仓储、映射 UI DTO 后再调用 attach；不要把 attach 到一个 501 接口当作迁移完成。

UI 遗留字段与 wire DTO 的映射：`task.path → source`、`task.target → destination`、`type=完整备份 → options.mode=full`、UI `status=done → wire state=completed`。UI 当前用产物路径作版本标识；后端引入 UUID 时应迁移索引并保留真实路径，不能只改显示 ID。

任务删除和版本索引删除默认只移除配置/索引；磁盘备份继续保留。未来若提供物理删除，需另设明确确认和独立接口，不把已有 DELETE 的含义暗改成递归删文件。

## 5. 六类自定义备份的语义

`FilterSpec` 包含 `paths / types / names / time / size / users` 六类。建议语义如下，需作为实现和测试的统一约定：

| 条件 | wire 字段 | 建议行为 |
| --- | --- | --- |
| 路径 | `paths.include / exclude` | 明确规则以源根目录为参照；禁止通过 `..` 越界；排除优先 |
| 类型 | `types` | 文件类别/扩展名，与特殊文件类型保真分开处理 |
| 名字 | `names` | 对文件名进行项目自实现的通配匹配，记录匹配规则版本 |
| 时间 | `time.field / from / to` | 明确修改/创建/访问时间，使用带时区的时间；不存在的时间属性不可捏造 |
| 尺寸 | `size.minBytes / maxBytes` | 以字节计；校验下限不超过上限；明确是否含边界 |
| 用户 | `users[{platform,id,...}]` | 按文件属主匹配，POSIX ID 与 Windows SID 分平台，不是网络登录用户名 |

同维度多选、跨维度取交集，未设置维度不限制；这是本项目建议约定，课件原图没有给出组合规则。先通过 `filters.validate` 验证，再通过 `filters.preview` 展示匹配/排除结果，最后用同一规则执行备份，避免预览和实际不一致。

当前这些端点均为 501，不能认为规则已生效。

## 6. 算法与数据格式的接入顺序

建议管线：扫描 → 筛选 → 条目与元数据 → 自实现打包 → 自实现压缩 → 库实现加密 → 校验/提交；还原按逆序处理。可选模块关闭时须明确为关闭，不能隐式选一个算法。

### 自实现打包

定义魔数、格式版本、字节序、条目类型、相对路径长度、数据长度与校验字段；保留空目录。解包前验证条目数量、路径、重复条目、数据范围和输出上限。拒绝绝对路径、目录穿越与越界链接。不得沿用临时内存指针或本机结构体内存布局作为跨平台磁盘格式。

### 自实现压缩

编码与解码必须一起交付：空输入、单符号输入、所有字节值、大文件、分块边界、截断或损坏码表等均应验收。解压受 `maxOutputBytes` 与资源配额限制，不能只相信文件头里的原始长度。用压缩率、耗时、峰值内存说明效果；不能仅证明“调用成功”。

### 允许库实现的加密

选用成熟的认证加密与密码派生实现。通过短期 `secretRef` 关联本次任务，不把明文密码放在路径、命令行参数、任务记录、manifest、日志、URL 或 localStorage。密文格式保存必要的非秘密参数，确保正确密码可还原，错误密码、篡改或不支持算法明确失败。`secrets.*` 当前为预留接口，尚没有实际密码保险箱。

### 保持已有版本可恢复

当前目录型备份格式是 `BACKNEST 1`，清单采用 UTF-8 路径、文件大小、CRC32。新容器与加密格式必须有自己的版本标识；不能直接改变 v1 解释而让已有备份失效。添加新格式读取器，保留旧格式读取与回归测试。

## 7. 定时、实时与网络的接入注意事项

- 定时：`schedules.*` 预留任务关联、时区、表达式、启用状态与错过任务策略。说明应用运行中、最小化、关闭窗口、真正退出后的行为。当前没有后台服务。
- 实时：`watchers.*` 预留启动、状态和停止；实现去抖、文件写入稳定检测、监听重建和备份目录排除，避免“备份引发备份”的循环。
- 并发：同一任务互斥；调度和手动触发共享同一执行器。取消前不要把任务标为成功；取消后报告保留/清理了哪些临时产物。
- 网络：`remotes.*` 提供配置、连接测试、传输与续传入口；远端元数据由 `remoteCatalog.*` 管理。
- 用户：`users.* / auth.*` 管理远端账户和会话；这与本机文件属主过滤不同。
- 传输使用受验证的加密通道和明确的账户权限；不得把当前本机 demo 桥直接公开到公网。
- 网络评分细则部分被遮挡；这里的协议和存储划分是实现建议，不能擅自承诺全部加分项。

## 8. 平台与当前已知限制

- 文件系统与权限抽象应分出 macOS/POSIX 和 Windows 适配器。普通复制逻辑可共用，属主、ACL、链接、扩展属性与文件占用语义不能假设一致。
- 当前目录路径中有符号链接会被拒绝。macOS `/tmp` 等别名可使用真实路径；这不是完整链接保真的实现。
- 当前源目录变化检测是前后清单/大小/修改时间比较，不是原子快照，也不是针对恶意并发替换的完整 TOCTOU 防护。
- 当前 job 在服务进程内存中，重启后无法恢复；UI 只能在同一服务存活时继续轮询。已完成磁盘备份可独立导入。
- 当前复制无 UI 取消能力。请等待文件操作完成再关闭本地服务；进程崩溃可能留下 `.partial-*` 目录，但不作为成功版本。
- 已完成版本不会自动从磁盘删除；浏览器保留版本数量的旧设置不能当成已实现的磁盘清理策略。
- 不承诺同名覆盖或跨系统完整恢复属主/权限；当前还原政策只有 `require_empty`。

## 9. 开发与验收流程

```sh
python3 server.py
python3 -m unittest discover -s tests/native -v
python3 scripts/generate_api_contract.py
python3 scripts/demo.py
```

修改接口时：先改 `api_contract.py` → 生成 OpenAPI/JS/类型/需求表 → 改具体 handler 与 C++ 适配器 → 加真实算法测试 → 更新能力标记 → 浏览器验证。不要先将 `implemented` 改为 true 以点亮按钮。

`tests/backend-ports.html` 验证前端全部声明操作的传输映射、501、参数编码、Qt transport 与工作区存储替换；只使用隔离的 mock，不创建真实备份。可用临时静态服务打开此测试页。`tests/state-migration.html` 保留旧 UI 数据迁移检查。它们不是产品功能演示。

每个扩展交付至少包括：实现代码、真实输入输出、错误/损坏输入处理、边界测试、协议/格式说明，以及“算法是否自行实现”的证据。禁止用延时器模拟完成、固定统计、空实现返回成功或只写接口就标记算法完成。

## 10. 接手后的优先顺序

1. 用当前 CLI 与 UI 完成真实基础往返，确认开发环境一致。
2. 接入 C++ `IBackupEngine` 适配器和 Qt transport，保持接口 ID 与 JSON DTO 稳定。
3. 落地后端持久化、真实取消及 Windows 实机验证。
4. 实现六维筛选、元数据/文件类型适配。
5. **自实现**打包解包和压缩解压，之后接入允许库实现的加密解密。
6. 再实现定时、实时、增量和网络模式，每阶段更新能力与验收表。
