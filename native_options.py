"""Validate supported policies before any engine write; serialize native options."""
from datetime import datetime
import os


def native_options(options):
    rows = []
    def add(key, value):
        value = str(value)
        if '\n' in value or '\r' in value or '\0' in value:
            raise ValueError('Filter values must not contain newlines/NUL.')
        rows.append(key + ' "' + value.replace('\\', '\\\\').replace('"', '\\"') + '"\n')
    filters = options.get('filters', {})
    for group, key in [('include', 'include'), ('exclude', 'exclude')]:
        for value in filters.get('paths', {}).get(group, []):
            add(key, value)
    for group, key in [('names', 'name'), ('types', 'type')]:
        for value in filters.get(group, []):
            if group == 'types' and value not in ('regular', 'directory'):
                raise ValueError('支持的类型为 regular / directory；扩展名请使用名字规则。')
            add(key, value)
    sizes = filters.get('size', {})
    if sizes.get('minBytes', 0) > sizes.get('maxBytes', 2**64 - 1):
        raise ValueError('尺寸下限不能大于上限。')
    for name, key in [('minBytes', 'min'), ('maxBytes', 'max')]:
        if name in sizes:
            if sizes[name] >= 2**64:
                raise ValueError('尺寸超出 64 位范围。')
            add(key, sizes[name])
    times = filters.get('time', {})
    if times and times.get('field') != 'modified':
        raise ValueError('当前仅支持修改时间筛选。')
    stamps = {}
    for name, key in [('from', 'after'), ('to', 'before')]:
        if name in times:
            stamp = datetime.fromisoformat(times[name].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('时间必须包含时区。')
            stamps[name] = int(stamp.timestamp())
            add(key, stamps[name])
    if stamps.get('from', -2**63) > stamps.get('to', 2**63-1):
        raise ValueError('时间下限不能大于上限。')
    for owner in filters.get('users', []):
        if owner['platform'] != ('windows' if os.name == 'nt' else 'posix'):
            raise ValueError('属主筛选必须使用当前平台的 SID / UID。')
        add('owner', owner['id'])
    for name, algorithm, key in [('archive', 'bkn-container-v2', 'archive'), ('compression', 'rle-block-v1', 'compress')]:
        codec = options.get(name)
        if codec:
            if codec['algorithm'] != algorithm or codec.get('blockBytes', 65536) != 65536:
                raise ValueError('不支持的算法或块大小。')
            add(key, 1)
    limits = [o['maxOutputBytes'] for name in ('archive', 'compression') if (o := options.get(name)) and 'maxOutputBytes' in o]
    if limits:
        if min(limits) > 2**63 - 1:
            raise ValueError('输出资源上限过大。')
        add('limit', min(limits))
    for name in options.get('metadata', {}).get('preserve', []):
        if name not in ('times', 'permissions'):
            raise ValueError('仅支持修改时间及基本权限；owner/ACL/xattrs 不支持。')
        add(name, 1)
    policy = options.get('fileTypes', {})
    for name, value in policy.items():
        if value != {'symlinks': 'reject', 'hardlinks': 'copy_contents', 'specialFiles': 'reject'}[name]:
            raise ValueError('链接按内容复制，符号链接、重解析点及特殊文件不支持。')
    text = ''.join(rows)
    if len(text.encode('utf-8')) > 65536:
        raise ValueError('筛选条件过多。')
    return text
