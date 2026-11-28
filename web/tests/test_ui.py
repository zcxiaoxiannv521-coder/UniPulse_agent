from copy import deepcopy
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from web.client import initial_workspace

APP = Path(__file__).resolve().parents[1] / 'app.py'


class PageTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'UNIPULSE_API_BASE':'', 'UNIPULSE_LOCAL_TEST':'', 'UNIPULSE_API_TOKEN':''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = AppTest.from_file(str(APP), default_timeout=45).run()

    def page(self, label):
        self.app.sidebar.radio(key='page').set_value(label).run()
        self.assertFalse(self.app.exception)

    def click(self, label):
        next(b for b in self.app.button if b.label == label).click().run()
        self.assertFalse(self.app.exception)

    def test_home_has_four_page_navigation_and_visible_timetable(self):
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.sidebar.radio(key='page').options, ['首页','课表','计划','任务'])
        self.assertEqual(len(self.app.chat_input), 0)
        text = '\n'.join(m.value for m in self.app.markdown)
        self.assertIn('<table class="week">',text)
        self.assertIn('正式计划 第1版',text)
        self.assertIn('高等数学',text)
        records = next(e for e in self.app.expander if e.label=='通知证据与处理记录')
        self.assertFalse(records.proto.expanded)
        self.assertTrue(any('尚未连接真实智能助手' in c.value for c in self.app.caption))

    def test_multiple_goals_render_and_selectable(self):
        """多目标时侧栏出现目标选择器，页面不应崩溃。"""
        state = initial_workspace()
        state["goals"].append({
            "id": "goal_second", "title": "准备大学英语四级",
            "event_id": "exam_ml", "daily_max_minutes": 60,
            "scope": "单词与听力", "availability": "每天 21:00 后",
            "status": "active", "version": 1,
        })
        with patch("web.client.initial_workspace", return_value=state):
            app = AppTest.from_file(str(APP), default_timeout=45).run()

        self.assertFalse(app.exception)
        selector = app.sidebar.selectbox(key="goal_select")
        self.assertEqual(selector.value, "goal_ml")

        app.sidebar.radio(key="page").set_value("计划").run()
        self.assertFalse(app.exception)

    def test_api_mode_week_uses_backend_time(self):
        """浏览器在 11/26，但后端 generated_at 是 11/30：课表要按后端周显示。"""
        state = initial_workspace()
        state["generated_at"] = "2026-11-30T09:00:00+08:00"
        fake = Mock()
        fake.is_demo = False
        fake.snapshot.return_value = state

        with patch("web.client.make_client", return_value=fake):
            app = AppTest.from_file(str(APP), default_timeout=45).run()

        self.assertFalse(app.exception)
        captions = "\n".join(c.value for c in app.caption)
        self.assertIn("11 月 30 日 至 12 月 06 日", captions)
        text = "\n".join(m.value for m in app.markdown)
        self.assertIn("高等数学", text)

    def test_api_mode_missing_generated_at_is_reported(self):
        state = initial_workspace()
        state.pop("generated_at", None)
        fake = Mock()
        fake.is_demo = False
        fake.snapshot.return_value = state

        with patch("web.client.make_client", return_value=fake):
            app = AppTest.from_file(str(APP), default_timeout=45).run()

        self.assertFalse(app.exception)
        errors = "\n".join(e.value for e in app.error)
        self.assertIn("无法确定本周日期范围", errors)

    def test_all_four_pages_render(self):
        for label in ['首页','课表','计划','任务']:
            with self.subTest(page=label):
                self.page(label)

    def test_navigation_and_refresh_do_not_write_data(self):
        before = deepcopy(self.app.session_state['demo_workspace'])
        for _ in range(6):
            for label in ['课表','计划','任务','首页']:
                self.page(label)
        self.click('刷新当前数据')
        self.assertEqual(before,self.app.session_state['demo_workspace'])

    def test_home_shortcut_opens_plan_page(self):
        self.click('查看完整计划')
        self.assertEqual(self.app.sidebar.radio(key='page').value,'计划')
        self.assertEqual(self.app.title[0].value,'我的计划')

    def test_progress_form_keeps_completed_task_and_home_reflects_it(self):
        self.page('任务')
        self.app.radio(key='progress_mode').set_value('报告明确进度').run()
        self.app.number_input(key='remain_t1').set_value(30)
        next(w for w in self.app.text_area if w.label=='进度说明').set_value('第三章只完成一半，剩余30分钟。')
        self.click('保存明确反馈')
        state = self.app.session_state['demo_workspace']
        self.assertEqual(state['tasks'][0]['status'],'partial')
        self.assertEqual(state['tasks'][0]['remaining_minutes'],30)
        self.assertEqual(state['tasks'][2]['status'],'done')
        self.page('首页')
        self.assertTrue(any('计划待调整' in w.value for w in self.app.warning))
        self.assertEqual(self.app.metric[1].value,'200 分钟')

    def test_time_only_does_not_change_progress(self):
        self.page('任务')
        self.app.number_input(key='time_t1').set_value(30)
        self.click('保存学习时长')
        self.click('保存学习时长')
        state = self.app.session_state['demo_workspace']
        self.assertEqual(state['tasks'][0]['actual_minutes'],30)
        self.assertEqual(state['tasks'][0]['remaining_minutes'],60)
        self.assertEqual(state['tasks'][0]['status'],'todo')
        self.assertEqual(state['plans'][0]['status'],'confirmed')

    def test_preview_evidence_cancel_then_confirm_and_cross_page_result(self):
        self.page('计划')
        self.click('生成修改预览')
        state = self.app.session_state['demo_workspace']
        self.assertEqual(state['pending'][0]['capacity']['unplaced_minutes'],80)
        self.assertEqual(state['pending'][0]['source_notice_ids'],['n_change'])
        self.assertEqual(state['events'][0]['version'],1)
        self.page('首页')
        self.assertEqual(self.app.metric[2].value,'1')
        self.click('取消草稿')
        self.assertEqual(self.app.session_state['demo_workspace']['events'][0]['start_at'],'2026-12-04T14:00:00+08:00')
        self.page('计划')
        self.click('生成修改预览')
        self.click('确认本次调整')
        state = self.app.session_state['demo_workspace']
        self.assertEqual(state['events'][0]['start_at'],'2026-12-02T14:00:00+08:00')
        self.assertEqual([r['status'] for r in state['reminders']],['invalidated','pending'])
        self.assertEqual(state['plans'][0]['version'],1)
        self.assertEqual(state['plans'][0]['status'],'needs_adjustment')
        self.page('首页')
        self.assertTrue(any('最近修改结果' in h.value for h in self.app.subheader))
        self.assertEqual(self.app.metric[2].value,'0')
        self.page('课表')
        self.assertIn('旧学习块','\n'.join(m.value for m in self.app.markdown))

    def test_stale_preview_confirm_disabled(self):
        self.page('计划')
        self.click('生成修改预览')
        next(w for w in self.app.text_input if w.label=='目标名称').set_value('已变化的目标')
        self.click('保存目标输入')
        button = next(b for b in self.app.button if b.label=='确认本次调整')
        self.assertTrue(button.disabled)
        self.assertEqual(self.app.session_state['demo_workspace']['events'][0]['version'],1)

    def test_add_valid_course_is_visible_on_home(self):
        from datetime import time
        self.page('课表')
        next(w for w in self.app.text_input if w.label=='课程名称').set_value('示例研讨课')
        next(w for w in self.app.text_input if w.label=='教室或地点').set_value('D101')
        next(w for w in self.app.time_input if w.label=='开始时间').set_value(time(17))
        next(w for w in self.app.time_input if w.label=='结束时间').set_value(time(18))
        self.click('保存课程')
        self.page('首页')
        self.assertIn('示例研讨课','\n'.join(m.value for m in self.app.markdown))

    def test_invalid_course_shows_error(self):
        self.page('课表')
        next(w for w in self.app.text_input if w.label=='课程名称').set_value('重复的课程')
        self.click('保存课程')
        self.assertTrue(any('已有课程' in e.value for e in self.app.error))
        self.assertEqual(len(self.app.session_state['demo_workspace']['classes']),5)

    def test_failed_course_can_be_corrected_without_stale_error(self):
        from datetime import time
        self.page('课表')
        before = deepcopy(self.app.session_state['demo_workspace'])
        next(w for w in self.app.text_input if w.label=='课程名称').set_value('验收修正课程')
        self.click('保存课程')
        self.assertTrue(self.app.error)
        self.assertEqual(before, self.app.session_state['demo_workspace'])
        next(w for w in self.app.time_input if w.label=='开始时间').set_value(time(17))
        next(w for w in self.app.time_input if w.label=='结束时间').set_value(time(18))
        self.click('保存课程')
        self.assertFalse(self.app.error)
        self.assertTrue(self.app.success)
        self.page('首页')
        self.assertIn('验收修正课程','\n'.join(m.value for m in self.app.markdown))

    def test_task_filter_changes_only_view(self):
        self.page('任务')
        before = deepcopy(self.app.session_state['demo_workspace'])
        self.app.selectbox(key='task_filter').set_value('已完成').run()
        text = '\n'.join(m.value for m in self.app.markdown)
        self.assertIn('✓ 整理复习范围与课程资料',text)
        self.assertNotIn('□ 附加模拟练习',text)
        self.assertEqual(before,self.app.session_state['demo_workspace'])

    def test_chat_is_collapsed_and_does_not_claim_connected(self):
        self.page('计划')
        expander = next(e for e in self.app.expander if e.label=='网页备用对话')
        self.assertFalse(expander.proto.expanded)
        self.app.chat_input[0].set_value('帮我复习').run()
        self.assertFalse(self.app.exception)
        self.assertIn('未连接模型',self.app.session_state['chat_history'][-1]['content'])

    def test_independent_sessions_do_not_share_demo_changes(self):
        self.page('任务')
        self.app.number_input(key='time_t1').set_value(77)
        self.click('保存学习时长')
        other = AppTest.from_file(str(APP),default_timeout=45).run()
        self.assertEqual(other.session_state['demo_workspace']['tasks'][0]['actual_minutes'],0)

    def test_untrusted_query_id_does_not_select_a_user(self):
        self.app.query_params['user_id']='other-person'
        self.app.run()
        self.assertEqual(self.app.session_state['demo_workspace']['profile']['display_name'],'林同学')

    def test_api_mode_requires_session_credential_before_read(self):
        with patch.dict(os.environ,{'UNIPULSE_API_BASE':'http://127.0.0.1:8000'}),patch('web.client.ApiClient.snapshot') as read:
            app = AppTest.from_file(str(APP),default_timeout=45).run()
            self.assertFalse(app.exception)
            read.assert_not_called()
            self.assertEqual(app.text_input[0].label,'本次会话的测试访问凭据')

    def test_api_denial_does_not_show_demo_data(self):
        from web.client import ClientError
        with patch.dict(os.environ,{'UNIPULSE_API_BASE':'http://127.0.0.1:8000'}),patch('web.client.ApiClient.snapshot',side_effect=ClientError('没有权限访问这项数据。')):
            app = AppTest.from_file(str(APP),default_timeout=45)
            app.session_state['api_token']='test-token'
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.error)
            self.assertNotIn('林同学','\n'.join(m.value for m in app.markdown))


if __name__=='__main__':
    unittest.main()
