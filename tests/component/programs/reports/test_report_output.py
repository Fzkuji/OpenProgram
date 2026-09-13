"""Report display keeps internal state out of successful user-facing bodies."""
from openprogram.programs.workflow.report_output import body


def test_single_child_returns_only_body():
    assert body({'results':{'tencent':{'status':'DRAFT_READY','summary':'真实进展','path':'/private/path'}}}) == '真实进展'


def test_failed_report_cannot_look_like_success():
    result = body({'status':'WAITING_MODEL','reason':'模型暂不可用','resume_task':{'resume':'/checkpoint'}})
    assert '尚未完成' in result and '/checkpoint' in result


def test_personal_body_omits_internal_source_appendix(tmp_path):
    p = tmp_path/'report.md'
    p.write_text('本周工作\n\n## 来源与周次\n\n```json\n{}\n```')
    assert body({'status':'DRAFT_READY','detail':'DRAFT_READY\n'+str(p)}) == '本周工作'


def test_partial_suite_keeps_total_resume_checkpoint():
    rendered = body({'status':'PARTIAL','results':{'tencent':{'status':'NEEDS_INPUT','questions':['缺少本周材料']}},
                     'resume_task':{'resume':'/suite/checkpoint'}})
    assert '缺少本周材料' in rendered and '/suite/checkpoint' in rendered


def test_completed_suite_omits_internal_checkpoint():
    assert body({'status':'COMPLETED','results':{'tencent':{'status':'DRAFT_READY','summary':'本周进展'}},
                 'resume_task':{'resume':'/internal/checkpoint'}}) == '本周进展'
