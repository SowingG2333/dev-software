# P0–P4 验证记录

日期：2026-11-15。环境：Windows、MSVC 14.37、Python 3.12、Qt 6.5.3。

## 自动验证

`python -m unittest discover -s tests/native -q`：36 项运行，34 项通过，2 项跳过（Windows 下的 POSIX 权限、符号链接条件）。本地输出见 `artifacts/p0-p4-tests.txt`。

覆盖基础备份/还原、独立版本、中文与空文件/目录、损坏和恶意清单、目录重叠、非空目标拒绝、本机 API 边界、契约生成一致性、SQLite 重启与修订冲突、取消不发布版本、六维筛选预览与实际清单一致、自定义容器及 RLE。

压缩样本包含空输入、单字符、重复数据、随机 1 MiB、16 MiB 数据，使用独立 Python 解码器交叉验证。坏包、截断、非法游程、路径越界和资源上限均有拒绝测试。16 MiB 测试不代表已做数 GiB 压力测试。

真实 HTTP 测试完成筛选预览 → 压缩备份 → 校验 → 还原 → 服务重启后读取工作区，并验证直接编解码端点及非法选项无副作用。

`python scripts/demo.py`：独立 SHA-256 和目录结构比较通过，源文件不变。`node --check` 已检查 app.js、native.js、features.js。

## 桌面验证与限制

Windows Qt 程序已实际编译启动；界面显示“Qt WebChannel · C++”。新增 `python scripts/check_desktop.py`：使用真实 QWebEnginePage、QWebChannel、生产 C++ ApplicationService 和前端 desktop-transport.js，在隔离临时工作区完成筛选预览、压缩备份、校验、还原及配置保存，退出后用 Python 独立比较目录和 SHA-256。结果 PASS，记录见 `artifacts/qt-check.txt`。测试不依赖鼠标操作，也不使用模拟 transport。

前端接口映射与状态迁移另有 14 项隔离检查通过；这些测试不替代真实文件操作验证。

根据用户最新要求，此次只验收 Windows。新版 P2–P4 未在 macOS 实机运行，不对该平台宣称通过。当前桌面运行还依赖 Python 本机服务和 Qt，不是独立安装包。
