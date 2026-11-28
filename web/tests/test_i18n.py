from web.i18n import change_value, status_label, system_text


def test_backend_statuses_have_chinese_labels():
    assert status_label('parsed') == '已解析'
    assert status_label('queued') == '待发送'
    assert status_label('unrecognized_status') == '其他状态'
    assert status_label('待老师确认') == '待老师确认'


def test_system_fields_and_versions_are_translated_without_rewriting_identifiers():
    assert system_text('Agent 已生成 v2，status: confirmed') == '智能助手 已生成 第2版，状态: 已确认'
    assert system_text('后端工作台数据缺少字段：study_blocks') == '后端工作台数据缺少字段：学习时间块'
    assert system_text('编号 task_v1，API_client') == '编号 task_v1，API_client'


def test_preview_times_use_chinese_dates_and_campus_timezone():
    assert change_value('2026-12-01T01:30:00+00:00') == '2026年12月01日 09:30'
    assert change_value('pending') == '待处理'
