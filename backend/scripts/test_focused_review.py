import unittest
from app.schemas.requirement_analysis import RequirementDraft
from app.services.focused_review import apply_review_policy, rule_issues
from app.services.requirement_analysis import confirmation_errors, validate_evidence
from scripts.test_requirement_analysis import draft

class FocusedReviewTests(unittest.TestCase):
    def setUp(self):
        self.d = RequirementDraft.model_validate(draft())
    def test_clear_rule_needs_no_scene_tick_but_inferred_stays_pending(self):
        apply_review_policy(self.d, [])
        self.assertEqual([r.status for r in self.d.rules], ['confirmed', 'pending'])
        self.assertFalse(self.d.scenarios[0].reviewed)
        self.assertEqual(confirmation_errors(self.d, []), [])
    def test_global_conflict_prevents_automatic_inclusion(self):
        self.d.questions[0].rule_ids = []
        apply_review_policy(self.d, [])
        self.assertTrue(all(r.status == 'pending' for r in self.d.rules))
        self.d.questions[0].answer = '按原文显示确认框'
        apply_review_policy(self.d, [])
        self.assertEqual(self.d.rules[0].status, 'confirmed')
    def test_incomplete_or_uncertain_image_never_auto_included(self):
        self.d.scenarios[0].then = ''
        apply_review_policy(self.d, [])
        self.assertEqual(self.d.rules[0].status, 'pending')
        self.d.scenarios[0].then = '文件保留'
        self.d.rules[0].source_material_ids = ['IMG1']
        visuals = [{'id':'IMG1','status':'uncertain'}]
        apply_review_policy(self.d, visuals)
        self.assertEqual(self.d.rules[0].status, 'pending')
        self.d.rules[0].review_note = '已对照原图，点击删除显示确认框'
        apply_review_policy(self.d, visuals)
        self.assertEqual(self.d.rules[0].status, 'confirmed')
    def test_bad_evidence_and_exclusions_are_preserved(self):
        validate_evidence(self.d, '不匹配的正文', [])
        apply_review_policy(self.d, [])
        self.assertEqual(self.d.rules[0].status, 'pending')
        self.d.rules[0].status = 'excluded'
        apply_review_policy(self.d, [])
        self.assertEqual(self.d.rules[0].status, 'excluded')
        self.assertTrue(confirmation_errors(self.d, []))
    def test_edit_reopens_only_affected_rule(self):
        apply_review_policy(self.d, [])
        self.d.rules[0].expected = ''
        apply_review_policy(self.d, [])
        self.assertEqual(self.d.rules[0].status, 'pending')
        self.assertTrue(rule_issues(self.d, self.d.rules[0], []))
