# 归档 BackNest：C++ 基础版 demo

已经实现真实的目录树备份与还原。C++17 引擎负责扫描、复制、生成清单和内容校验；现有 Web UI 通过一个仅监听本机的 Python 标准库通信桥调用引擎。

本轮在 **macOS Apple Silicon** 上编译并验证。未安装 Qt，因此当前在浏览器中操作，尚未生成 Qt 桌面程序、macOS 安装包或 Windows EXE。Windows 已提供 CMake/MSVC 构建入口，尚未实机验证。

## 启动界面

macOS 已安装 Xcode Command Line Tools、Python 3 后，在项目目录运行：

```sh
python3 server.py
```

首次启动自动编译 C++ 引擎，不需要 Node.js、CMake 或第三方 Python 包。打开 [本地界面](http://127.0.0.1:4173/)。右上角应显示“本机引擎 · C++”。按 `Ctrl+C` 停止服务；执行文件操作时请等待完成后再退出。

如果端口被占用，可以改用：

```sh
python3 server.py --port 4175
```

单独运行 `python3 -m http.server` 只能查看界面，不能执行备份。连接真实引擎需要运行 `server.py`。

## 使用流程

1. **新建备份**：填写名称，输入本机绝对路径或点击“浏览”选择源目录和备份位置。目标位置可以是尚不存在的新目录。
2. **创建任务**：确认完整备份、手动执行与内容校验。创建本身不复制文件。
3. **执行备份**：点击任务右侧“运行”。进度来自 C++ 的实际文件操作，只有校验和提交成功才显示“已完成”。
4. **查看版本**：每次成功备份都会在备份位置生成一个独立的 `snapshot-*` 目录；失败不会新增成功版本。
5. **数据还原**：选择历史版本，填写新目录或空目录，点击“开始还原”。引擎先验证备份，再写入目标位置。
6. **导入备份**：如果界面任务记录已清空，在“数据还原”页点击“导入已有备份”，选择含 `manifest.bkn` 与 `data` 的 `snapshot-*` 目录。导入时会校验文件内容。

首次打开没有预置任务或日志。所有显示的成功版本都来自实际 C++ 备份或校验后的导入；用户任务配置和界面日志保存在当前浏览器。清空界面记录或删除任务不会删除磁盘上的备份。

## 快速运行完整样例

如果想先用生成的测试文件验证完整流程：

```sh
python3 scripts/demo.py
```

脚本在 `.backnest/demo-<随机标识>/` 新建测试文件，调用真实 C++ 引擎备份并还原，再独立比较 SHA-256 和目录结构。每次使用新目录，不触碰现有用户文件。输出源目录、备份目录、还原目录和验证报告；不会向 UI 预置任务。

## 单独使用 C++ 命令行

```sh
python3 scripts/build.py
./build/backnest backup "/绝对路径/源目录" "/绝对路径/备份位置"
./build/backnest verify "/绝对路径/备份位置/snapshot-实际版本标识"
./build/backnest restore "/绝对路径/备份位置/snapshot-实际版本标识" "/绝对路径/新的还原目录"
```

命令输出 JSON 行，含实际进度、成功结果或错误；成功退出码为 `0`，文件操作失败为 `1`，参数错误为 `2`。

也提供标准 CMake 构建：

```sh
cmake -S . -B build-cmake
cmake --build build-cmake --config Release
```

Windows 可在安装了 C++ 工具的 Visual Studio Developer PowerShell 中运行 `py scripts/build.py` 或 `py server.py`。C++ 使用 `wmain` 接收 Unicode 路径。Windows 平台的实际复制、权限和安装包还需要后续验证。

## 备份格式与数据处理

```text
备份位置/
└── snapshot-<唯一标识>/
    ├── manifest.bkn    # 格式版本、源目录、相对路径、文件大小与 CRC32
    └── data/           # 原始文件和目录层级，包括空目录
```

- 以 64 KB 缓冲流式复制文件，不一次性读入完整大文件。
- 在临时 `.partial-*` 目录写入，验证后通过重命名发布正式版本。失败清理本次临时目录，不报告成功。
- 备份前后比较源文件清单、大小和修改时间，检测操作期间常见的文件变化；这不是操作系统级原子快照，备份时应停止修改源文件。
- 还原前检查清单、文件集合、大小与 CRC32；拒绝相对路径越界、重复条目、缺失或多余文件。
- 还原使用临时目录，成功后提交；只接受新目标或空目录，不覆盖现有文件。
- CRC32 用于意外损坏检测，不是加密或防篡改认证。
- 所有版本保留，不自动清理旧版本。空间不足、权限不足和路径错误会明确失败。

## 本轮支持范围

支持普通文件、子目录、空目录、空文件、中文与空格文件名、独立完整备份版本、校验、导入与还原。

基础版**不支持**软链接和特殊文件、元数据完整保留、压缩、加密、增量、自定义六类筛选、自动调度或网络备份。遇到软链接或特殊文件会明确报错，不会悄悄跳过。目录路径中有符号链接时也会拒绝；macOS `/tmp` 等别名可使用解析后的真实路径。最多 200,000 个目录/文件条目。

本地通信桥只绑定 `127.0.0.1`，使用会话令牌、来源和 Host 检查；不能当作公网服务器。后续 Qt WebChannel 可以替换通信桥，保留 C++ 文件操作引擎。

## 验证

```sh
python3 -m unittest discover -s tests/native -v
python3 scripts/demo.py
```

25 项自动检查覆盖：目录往返、独立版本、空文件/空目录、Unicode、CRC32、目录重叠、拒绝覆盖、损坏/缺失/额外文件、恶意清单、符号链接、权限失败清理，以及真实 HTTP → C++ → 文件系统闭环和本地接口保护。另已在浏览器中完成创建、目录选择、备份和还原，独立比较还原结果的 SHA-256。

## 后端交接与接口

- [后端交接文档](docs/BACKEND_HANDOFF.md)：接手顺序、接口/DTO、平台边界和验收标准。
- [全量需求核验表](docs/REQUIREMENTS_AUDIT.md)：课件每项要求到后端端口的映射。
- [OpenAPI 契约](api/openapi.json)、[前端类型](api/backend-api.d.ts)、[C++ 服务接口](core/backend_interfaces.hpp)。
- `api_contract.py` 是唯一接口注册表，`python3 scripts/generate_api_contract.py` 生成其余契约与调用端。
- 当前 63 个接口中 7 个已实现、56 个明确返回 501；扩展能力不会伪造成功。现有 UI 调用已使用版本化接口。
- `backend-api.js` 支持 HTTP 和可替换的 Qt transport；`workspace-store.js` 为浏览器配置提供后端持久化替换端口。实际后端数据库仍未实现。

**算法要求：打包解包、压缩解压等核心算法必须自行实现，不能用现成库或系统命令包装代替；加密解密可以调用成熟库。** 标准文件系统、Qt、通信/序列化和数据库等基础设施可使用库。测试中的对照库不得进入产品压缩执行路径。

隔离前端检查可用一个临时静态服务打开 `tests/backend-ports.html` 和 `tests/state-migration.html`；这些检查不会向 UI 添加预置备份。

## 项目文件

| 文件 | 职责 |
| --- | --- |
| `core/main.cpp` | 无第三方依赖的 C++17 备份/还原/校验引擎 |
| `CMakeLists.txt` | 跨平台 C++ 构建入口 |
| `server.py` | 临时本地通信桥与任务执行状态 |
| `scripts/build.py` | 自动使用本机编译器构建 |
| `scripts/demo.py` | 生成独立测试文件并运行完整样例 |
| `native.js` | UI 与统一后端端口、目录浏览和实际进度的连接 |
| `api_contract.py` / `api/` | 全量版本化端口、请求/响应 DTO 与生成的 OpenAPI |
| `backend-api.js` | 生成的前端统一调用入口与 Qt transport 替换点 |
| `workspace-store.js` | 浏览器配置与后端持久化替换端口 |
| `core/backend_interfaces.hpp` | 各扩展模块的 C++ 抽象服务接口 |
| `app.js` / `styles.css` | 任务界面、交互与视觉样式 |
| `state.js` | 初始空状态与旧版虚构数据迁移 |
| `tests/native/` | 真实引擎和接口的自动检查 |
| `IMPLEMENTATION_PLAN.md` | 课程要求、当前范围与后续路线 |
