/* Browser-local task configuration. No sample backups or completed runs. */
'use strict';

function createInitialState() {
  return {
    schemaVersion: 2,
    tasks: [],
    logs: [],
    settings: {
      compression: false,
      encryption: false,
      notification: true,
      verify: true,
      keep: 10,
      target: '',
      theme: '浅色',
    },
  };
}

function migrateStoredState(stored) {
  if (!stored || !Array.isArray(stored.tasks) || !Array.isArray(stored.logs) || !stored.settings) {
    return createInitialState();
  }
  const next = structuredClone(stored);
  if (next.schemaVersion >= 2) return next;

  // Match the original seed IDs AND paths; never remove a user's task by name.
  const seeds = {
    work: '/Users/demo/Documents/工作文档',
    photos: '/Users/demo/Pictures/生活相册',
    project: '/Users/demo/Projects/BackNest',
    study: '/Users/demo/Documents/学习资料',
  };
  const seedLogs = new Set([
    ['工作文档 · 备份演示完成', '1,284 个文件 · 增量备份 · 耗时 12 秒', '今天 10:32'],
    ['生活相册 · 备份演示完成', '3,620 个文件 · 完整备份 · 耗时 1 分 24 秒', '今天 09:15'],
    ['项目源码 · 备份演示完成', '746 个文件 · 增量备份 · 耗时 8 秒', '昨天 23:40'],
    ['学习资料 · 备份演示完成', '532 个文件 · 完整备份 · 耗时 32 秒', '昨天 20:18'],
  ].map(entry => JSON.stringify(entry)));
  next.tasks = next.tasks
    .filter(task => !(Object.hasOwn(seeds, task.id) && seeds[task.id] === task.path))
    .map(task => ({
      ...task,
      // All versions produced by v1 were simulations, including user-created tasks.
      versions: [],
      status: 'ready',
      progress: 0,
      time: '尚未运行',
      files: null,
      size: '未读取',
      sourceScanned: false,
    }));
  next.logs = next.logs
    .filter(entry => !seedLogs.has(JSON.stringify([entry.title, entry.detail, entry.time])))
    .map(entry => {
      if (/备份演示完成|还原流程演示完成/.test(entry.title)) {
        return {
          ...entry,
          title: entry.title.replace('备份演示完成', '备份界面演示').replace('还原流程演示完成', '还原界面演示'),
          detail: '旧版界面操作记录，未创建或还原真实备份。',
          level: 'info',
        };
      }
      return entry;
    });
  if (!next.tasks.length && next.settings.target === '/Volumes/Backup') next.settings.target = '';
  next.settings = { ...createInitialState().settings, ...next.settings };
  next.schemaVersion = 2;
  return next;
}
