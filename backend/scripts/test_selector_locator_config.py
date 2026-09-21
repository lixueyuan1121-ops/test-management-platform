import unittest
from app.services.selector_ranking import normalize_candidate, candidate_identity, order_candidates, is_valid_candidate
from app.schemas.selector import SelectorKeyIn

class LocatorConfigTests(unittest.TestCase):
    def test_round_trip(self):
        for by in ('testid', 'xpath', 'css'):
            c = dict(by=by, value='target', has_text='保存', nth=1, primary=True)
            self.assertEqual(normalize_candidate(c), c)
            self.assertEqual(SelectorKeyIn(project_id=1, key='save', candidates=[c]).model_dump()['candidates'], [c])
            self.assertNotEqual(candidate_identity(c), candidate_identity({**c, 'nth':0}))
            self.assertNotEqual(candidate_identity(c), candidate_identity({**c, 'has_text':'取消'}))
    def test_validation_and_priority(self):
        c = dict(by='xpath', value='//button', primary=True)
        self.assertEqual(order_candidates([dict(by='testid',value='fallback'),c])[0], c)
        for extras in (dict(nth=-1),dict(nth=True),dict(nth=1.5),dict(has_text=''),dict(has_text=2),dict(primary='yes')):
            self.assertFalse(is_valid_candidate({**c, **extras}))

if __name__ == '__main__': unittest.main()
