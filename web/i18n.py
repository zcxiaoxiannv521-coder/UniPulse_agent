"""仅转换界面系统文案；不修改接口值、用户输入或通知原文。"""
import re
from datetime import datetime

STATUS_LABELS = {
    'todo':'待开始','doing':'进行中','partial':'部分完成','done':'已完成',
    'active':'执行中','draft':'草稿','needs_review':'待检查',
    'needs_adjustment':'计划待调整','needs_input':'待补充信息',
    'needs_clarification':'待补充信息','confirmed':'已确认','completed':'已完成',
    'pending':'待处理','invalidated':'已失效','cancelled':'已取消','applied':'已应用',
    'parsed':'已解析','unparsed':'待解析','received':'已接收','duplicate':'重复通知',
    'ignored':'已忽略','expired':'已过期','rejected':'未通过','superseded':'已被新版替代',
    'queued':'待发送','sent':'已发送','delivered':'已送达','failed':'发送失败',
    'scheduled':'已安排','planned':'已安排','locked':'已锁定','normal':'正常',
    'moved':'已调整','waiting':'等待处理','running':'处理中','error':'处理失败',
}
TERMS = {
    'Agent':'智能助手','API':'后台服务','Token':'访问凭据','token':'访问凭据',
    'payload':'输入内容','status':'状态','actual_minutes':'累计学习时长',
    'remaining_minutes':'剩余估时','daily_max_minutes':'每日学习上限',
    'daily_limit_minutes':'每日学习上限','start_at':'开始时间','end_at':'结束时间',
    'event_id':'考试编号','exam_id':'考试编号','task_id':'任务编号',
    'goal_id':'目标编号','confirm_code':'确认编号','expected_version':'数据版本',
    'version':'版本','profile':'用户资料','plans':'计划','goals':'目标',
    'events':'事件','classes':'课程','tasks':'任务','study_blocks':'学习时间块',
    'reminders':'提醒','notices':'通知','activity':'处理记录',
    'generated_at':'数据生成时间','demo_now':'示例时间',
    'scope':'复习范围','availability':'允许时段','title':'名称','feedback':'进度说明',
    'Not Found':'该功能尚未开放或记录不存在','Internal Server Error':'后台服务暂时出错',
}


def status_label(value):
    if value in STATUS_LABELS:
        return STATUS_LABELS[value]
    if isinstance(value,str) and re.search(r'[\u4e00-\u9fff]',value):
        return value
    return '其他状态'


def system_text(value):
    text=str(value)
    for source,target in TERMS.items():
        text=re.sub(r'(?<![A-Za-z0-9_])'+re.escape(source)+r'(?![A-Za-z0-9_])',lambda _:target,text)
    text=re.sub(r'(?<![A-Za-z0-9_])v(\d+)\b',r'第\1版',text)
    for source,target in STATUS_LABELS.items():
        text=re.sub(r'(?<![A-Za-z0-9_])'+re.escape(source)+r'(?![A-Za-z0-9_])',lambda _:target,text)
    return text


def change_value(value):
    if isinstance(value,str) and re.fullmatch(r'\d{4}-\d\d-\d\dT.*',value):
        try:
            dt=datetime.fromisoformat(value)
            if dt.tzinfo is not None:
                from web.client import TZ
                return dt.astimezone(TZ).strftime('%Y年%m月%d日 %H:%M')
        except ValueError:
            pass
    return system_text(value)
