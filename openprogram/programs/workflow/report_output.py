"""Human report bodies with an explicit structured integration mode."""
from __future__ import annotations

import json
from pathlib import Path
from openprogram.sandbox import validate_read_path
from openprogram.worktree.path_resolve import resolve_path


def structured(task):
    if not task.lstrip().startswith('{'):
        return False
    try:
        return json.loads(task).get('result_format') == 'structured'
    except (ValueError, AttributeError):
        return False


def _saved_body(result):
    detail = result.get('detail', '')
    path = result.get('path')
    if isinstance(detail, str) and detail.startswith('DRAFT_READY\n'):
        path = detail.split('\n', 1)[1].strip()
    elif path:
        path = str(Path(path) / 'summary.md')
    if not path:
        return ''
    resolved, _ = resolve_path(path)
    if validate_read_path(resolved):
        return '稿件已保存，但当前执行环境无法读取正文。'
    try:
        with open(resolved, encoding='utf-8') as handle:
            body = handle.read(100001)
        if len(body) > 100000:
            return '稿件已保存，正文超出本次显示长度。'
        return body.split('\n## 来源与周次', 1)[0].strip()
    except OSError:
        return '稿件已保存，但暂时无法读取正文。'


def body(result):
    children = result.get('results')
    if isinstance(children, dict):
        labels = {'personal':'个人周报', 'group':'小组汇报', 'tencent':'腾讯汇报'}
        rendered = (body(next(iter(children.values()))) if len(children) == 1 else
                    '\n\n'.join(labels.get(key, key) + '\n' + body(value) for key, value in children.items()))
        resume = result.get('resume_task', {})
        checkpoint = resume.get('resume') if isinstance(resume, dict) else None
        if result.get('status') != 'COMPLETED' and checkpoint:
            rendered += '\n总汇报可从已保存的检查点恢复：' + str(checkpoint)
        return rendered
    status = result.get('status')
    if status in ('DRAFT_READY', 'COMPLETED', 'PARTIAL'):
        summary = result.get('summary')
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        saved = _saved_body(result)
        if saved:
            return saved
    details = result.get('questions') or result.get('reasons') or result.get('issues')
    if isinstance(details, list):
        message = '\n'.join(str(x) for x in details)
    else:
        message = str(result.get('reason') or result.get('detail') or '还没有可交付的汇报正文。')
    checkpoint = result.get('resume_task', {}).get('resume')
    if checkpoint:
        message += '\n可从已保存的检查点恢复：' + str(checkpoint)
    return '汇报尚未完成。' + message
