# 核验记录

日期：2026-11-08。环境：Windows 10/11、Python 3.14、Zig 0.17.1（Clang/LLD，临时工具链在工作区 `.tools/`，不入库）、Windows CNG、Node.js 24（仅测试对照）。

本轮范围是 **P5 加密、P6 自动化与增量、P7 网络**；P4 打包/压缩已从本轮代码中移除，相关端点保留 501。

## 自动检查

```sh
python -m unittest discover -s tests -v
```

结果：**85 项通过，3 项跳过**（约 15 秒）。

跳过项：

- C++ 引擎符号链接测试；
- BNE1 加密符号链接测试（当前 Windows 账户没有创建符号链接权限）；
- POSIX 权限测试。

覆盖：

- 基础目录备份/还原、中文/空文件/空目录、独立版本、CRC32、坏清单、缺失/额外文件、非空还原目标、鉴权、Host/Origin、v1 契约。
- P5：BNE1 空/1 字节/中文/1 MiB 往返、错误密码、salt/nonce/密文/tag/header 篡改、截断、目标已存在、密码不泄漏、`maxOutputBytes`；Node.js AES-256-GCM 独立向量交叉验证。
- P6：增量硬链接、变更/新增/删除预览、各版本独立还原、删除基准后仍可还原；cron 步长/列表/周日、时区回退、错过策略、忙时跳过、非法表达式停用、去抖触发、目标目录排除、版本保留。
- P7：TLS 验证、正确/错误登录、用户隔离、上传/下载往返、上传续传、Range 下载续传、分块篡改 finalize 失败、跨用户删除 404、HTTP API。
- HTTP：新增 operationId、400/401/404/501、任务状态、secretRef 生命周期、远端配置/目录、活动日志、概览。

## C++ 构建

本机使用 Zig 0.17.1 的 `zig c++`（Clang/LLD，MinGW 目标）构建：

```sh
zig c++ -std=c++17 -O2 -municode core/main.cpp -o build/backnest.exe
```

- C++ 引擎测试 12 项通过（2 项平台权限跳过）；
- `/api/session` 返回 `engine = C++17`；
- C++ 完整备份/校验/还原通过；
- Python 增量快照可被 C++ verify/restore 读取；
- `build/` 被 git 忽略，二进制不入库。

## P7 远端验证

- 独立 HTTPS 服务，默认 `127.0.0.1`，首次运行在 `.backnest/tls/` 生成自签名测试证书。
- 用户 `alice`/`bob` 隔离：bob 的 catalog 为空，下载/删除 alice 快照返回 404。
- 上传中断后续传：`X-Offset` 从已有字节继续。
- 下载 Range 从已有偏移继续。
- 篡改任一分块，finalize 时 SHA-256 不一致。
- 不提供 CA 时自签名 TLS 验证失败；提供 CA 后成功。
- 测试证书/私钥只在运行时生成于被忽略的 `.backnest/tls/`，不提交仓库，不能用于生产。

## 契约检查

- 13 项课件需求均有 operationId 映射。
- 63 个 operationId：50 个已实现，13 个预留；预留返回结构化 501，不创建 job 或写文件。
- 能力信息与注册表一致；P4 算法列表为空，P5 算法只公布实际实现。
- 生成产物与注册表一致。
- 持久化任务 DTO 不允许保存临时 secretRef。

## 端到端演示

```sh
python scripts/demo_extensions.py
```

执行 P7 创建远端/TLS 测试/上传/下载；P5 加密/解密/错误密码；P6 增量/校验/还原、cron、实时监听。产物保留在 `.backnest/demo-ext-*`。

## 前端

- `app.js` / `native.js` / `backend-api.js` 通过 `node --check`。
- “扩展能力”页已接入 P5/P6/P7 接口和真实任务轮询。
- 本环境无法启动 Chromium（沙箱禁止命名管道），未做真实浏览器点击/截图验证；需在目标机器人工复核。

## 未验证/未完成

- macOS P5–P7 复测。
- 浏览器真实点击与视觉回归。
- P4 打包/压缩（不在本轮）。
- 加密大文件分块（当前整文件读入内存）。
- P7 配额、带宽限制、角色 ACL 管理界面、token 持久化、真实证书部署。
- 六维筛选、元数据、Qt 桌面、系统服务式定时、可靠取消。
- 跨系统恢复属主/权限。

## 数据与隐私

测试文件、演示产物和 `.backnest/` 均在被 git 忽略的本地目录。密码不进入日志、任务记录、manifest 或 localStorage；`secretRef` 默认 600 秒后过期。P7 远端用户密码只保存 PBKDF2 加盐哈希；登录 token 仅在内存。
