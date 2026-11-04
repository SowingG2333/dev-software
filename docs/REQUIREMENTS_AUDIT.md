# 需求与后端接口覆盖表

由 `api_contract.py` 生成。`reserved` 表示接口预留，不表示算法已实现。

| 要求 | 分值 | 当前状态 | 后端 operationId |
| --- | --- | --- | --- |
| 目录树数据备份 | 基础共40分 | implemented_basic | `backup.start` |
| 目录树数据还原 | 基础共40分 | implemented_basic | `restore.start`, `snapshots.verify` |
| 文件类型支持 | 满分10分 | regular_files_and_directories_only | `fileTypes.inspect` |
| 属主、时间、权限等元数据 | 满分10分 | reserved | `metadata.read`, `metadata.apply` |
| 自定义备份：路径、类型、名字、时间、尺寸、用户 | 照片分值不清 | reserved | `filters.preview`, `filters.validate` |
| 打包解包 | 每种算法10分 | reserved | `archive.algorithms`, `archive.pack`, `archive.unpack`, `archive.list` |
| 压缩解压 | 每种算法10分 | reserved | `compression.algorithms`, `compression.compress`, `compression.decompress` |
| 加密解密 | 每种算法10分 | reserved | `encryption.algorithms`, `encryption.encrypt`, `encryption.decrypt`, `secrets.create`, `secrets.revoke` |
| 图形界面 | 满分10分 | web_ui_implemented_qt_pending | `system.capabilities`, `system.contract`, `filesystem.directories`, `jobs.get`, `jobs.cancel`, `tasks.list`, `tasks.create`, `tasks.update`, `tasks.delete`, `snapshots.list`, `snapshots.register`, `settings.get`, `settings.update`, `activity.list`, `activity.export`, `workspace.overview` |
| 定时备份 | 满分10分 | reserved | `schedules.list`, `schedules.create`, `schedules.update`, `schedules.delete`, `schedules.nextRuns` |
| 实时备份 | 满分15分 | reserved | `watchers.start`, `watchers.get`, `watchers.stop` |
| 网络备份，含用户与元数据管理 | 具体而定，部分遮挡 | reserved | `remotes.list`, `remotes.create`, `remotes.update`, `remotes.delete`, `users.list`, `users.create`, `users.update`, `users.delete`, `remotes.test`, `remotes.upload`, `remotes.download`, `remoteCatalog.list`, `remoteCatalog.register`, `remoteCatalog.get`, `remoteCatalog.delete`, `auth.login`, `auth.logout` |
| 其他功能：增量、版本管理等 | 按难度讨论 | versions_basic_other_reserved | `snapshots.delete`, `incremental.preview` |
