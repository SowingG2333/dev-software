# 归档 BackNest · P0–P4

本地目录备份工具：真实 C++17 文件引擎、浏览器界面及 Qt WebChannel 桌面外壳、SQLite 工作区持久化、自实现六维筛选、自定义单文件容器与 RLE 压缩。当前交付范围为 P0–P4。

## 在这台 Windows 电脑试用

双击 `start-web.cmd` 自动打开浏览器；或双击 `start-desktop.cmd` 打开 Qt 桌面窗口。两种入口默认使用同一个 `.backnest` 工作区，请先关闭一个再启动另一个。浏览器版关闭网页不会关闭服务，在启动终端按 Ctrl+C 结束；桌面版关闭窗口会结束其私有服务。

需要 Python 3 和 MSVC C++ 工具链；当前机器已构建引擎和 Qt 程序。桌面入口还依赖本地 `.venv/Qt/6.5.3/msvc2019_64`，不是可复制到其他电脑直接运行的独立安装包。默认端口自动选择，仅监听本机。

```powershell
python scripts/launch_web.py
python scripts/desktop.py
```

## 试用步骤

1. 新建备份：填写名称、源目录、备份位置（绝对路径，源与备份位置不能互相包含）。
2. 备份选项：可设置路径、类型、名称、修改时间、大小、所有者六类筛选；点击预览检查清单。留空表示不限制。
3. 勾选单文件容器和压缩，创建任务后点击运行。成功生成独立 `.bka` 文件；未开启容器时生成 `snapshot-*` 目录。
4. 在数据还原中选择版本，填入新目录或空目录。还原会先校验内容，拒绝覆盖非空目标。
5. 退出并重开，检查任务、版本、设置仍在。任务运行时可取消；提交已开始时会明确拒绝迟到的取消。

也可运行 `python scripts/demo.py` 生成独立样例并比较源与还原文件的 SHA-256。样例不会自动添加到界面任务列表。删除界面记录不会删除磁盘备份。

## 实现范围

| 阶段 | 实现 |
| --- | --- |
| P0 | 目录备份、校验、还原、目录浏览、空目录/空文件/中文路径、独立版本、真实进度 |
| P1 | 统一 API 注册表、DTO、生成调用端/类型/OpenAPI、能力查询；预留功能明确返回 501 |
| P2 | Qt WebChannel → C++ 适配层 → 本机 Python 服务 → C++ 引擎；SQLite 持久化、修订冲突检测、任务查询及协作取消 |
| P3 | 六维自实现匹配、预览和执行共用筛选规则、修改时间与基本权限保留、明确拒绝不支持的文件类型 |
| P4 | 自定义 BKNARC2 容器、64 KiB 分块 RLE 编解码、CRC32、路径安全和资源上限校验 |

普通文件、目录和空目录可备份。符号链接、Windows 重解析点和特殊文件明确拒绝；硬链接按普通文件复制内容，不保留链接关系。仅保留修改时间和基本权限，不声称完整保留 ACL、扩展属性、创建时间、所有权或 NTFS 数据流。

筛选同一维度中多项为 OR，维度之间为 AND；名称/路径通配符区分大小写，`?` 匹配一个 Unicode 字符，`*` 可跨目录分隔符。所有者条件是 Windows SID 或 POSIX UID。保留匹配文件必需的父目录。文件正在变化时可能失败；本工具不提供操作系统级原子快照。

CRC32 用于意外损坏检测，不提供认证加密。默认容器解码上限 8 GiB、200,000 个条目。随机数据等不可压缩块保存为原始块，因此压缩不保证文件变小。加密、定时/实时、增量及网络备份不在此次范围内。

## 验证与文档

```powershell
python -m unittest discover -s tests/native -v
python scripts/demo.py
python scripts/check_desktop.py
python scripts/generate_api_contract.py
```

2026-11-15 Windows：36 项自动测试运行，34 项通过，2 项因平台条件跳过；独立样例 SHA-256/目录一致。Qt 已构建、启动，并通过真实 WebChannel 筛选→压缩备份→校验→还原→配置保存测试；独立 SHA-256 比较一致。按用户最新要求，此次仅验收 Windows；macOS 未验证，不计入此次交付条件。

- [逐项验收说明](docs/P0_P4_ACCEPTANCE.md)
- [容器和压缩格式](docs/FORMAT.md)
- [当前验证记录](docs/VALIDATION.md)
- [接口需求映射](docs/REQUIREMENTS_AUDIT.md)

实现位于 `core/main.cpp`、`core/policies.hpp`、`core/container.hpp`；应用服务在 `server.py`，持久化在 `repository.py`，桌面适配在 `desktop/main.cpp`。构建目录、测试备份、SQLite 数据及本机 Qt 依赖均不提交。
