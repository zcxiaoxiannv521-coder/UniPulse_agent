from copy import deepcopy
from datetime import date
import unittest
from web.client import DemoClient, initial_workspace
from web.view_model import agenda_for_day, assess_demo_blocks


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.state = initial_workspace()

    def test_sample_gap_is_derived_and_does_not_mutate(self):
        before = deepcopy(self.state)
        cap = assess_demo_blocks(self.state, 'exam_ml', '2026-12-02T14:00:00+08:00')
        self.assertEqual((cap['required_minutes'],cap['kept_minutes'],cap['unplaced_minutes']), (230,150,80))
        self.assertEqual(before,self.state)

    def test_gap_changes_with_deadline_and_allowed_slots(self):
        cap = assess_demo_blocks(self.state, 'exam_ml', '2026-12-03T14:00:00+08:00')
        self.assertEqual(cap['unplaced_minutes'], 0)
        self.state['availability'].pop(1)
        cap = assess_demo_blocks(self.state, 'exam_ml', '2026-12-02T14:00:00+08:00')
        self.assertEqual(cap['unplaced_minutes'], 170)

    def test_pending_draft_does_not_change_official_board(self):
        before = agenda_for_day(self.state,date(2026,12,2))
        DemoClient(self.state).preview_exam_change('exam_ml','2026-12-02T14:00:00+08:00',1,'n_change')
        self.assertEqual(before,agenda_for_day(self.state,date(2026,12,2)))

    def test_outdated_event_version_marks_blocks_as_old(self):
        self.state['events'][0]['version'] += 1
        rows = agenda_for_day(self.state,date(2026,11,30))
        self.assertTrue(any(r['kind']=='旧学习块' for r in rows))
        self.assertFalse(any(r['kind']=='学习' for r in rows))

    def test_completed_and_retired_blocks_are_not_upcoming_work(self):
        self.state['study_blocks'][0]['status']='completed'
        self.assertFalse(any(r['kind']=='学习' for r in agenda_for_day(self.state,date(2026,11,30))))
        self.state['study_blocks'][0]['status']='confirmed'
        self.state['tasks'][0]['status']='done'
        self.assertFalse(any(r['kind']=='学习' for r in agenda_for_day(self.state,date(2026,11,30))))


if __name__ == '__main__':
    unittest.main()
