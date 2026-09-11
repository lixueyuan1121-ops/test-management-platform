"""Dispatch grouping regression: single turns and independent generation batches."""
import unittest

from fastapi import HTTPException
from app.models import EvalQuery
from app.api.eval_queue import _dispatch_conversation_groups


def query(qid, turn=0, source=None, group="g1"):
    return EvalQuery(id=qid, project_id=1, ai_task_id=source, title="case", prompt="prompt",
                     conversation_group=group, turn_index=turn)


class ConversationModeTests(unittest.TestCase):
    def test_same_name_first_turns_are_single(self):
        self.assertEqual(_dispatch_conversation_groups([query(1), query(2)]), {1: None, 2: None})

    def test_multiturn_is_scoped_to_generation(self):
        rows = [query(1, 0, 10), query(2, 1, 10), query(3, 0, 20), query(4, 1, 20)]
        groups = _dispatch_conversation_groups(rows)
        self.assertEqual(groups[1], groups[2])
        self.assertEqual(groups[3], groups[4])
        self.assertNotEqual(groups[1], groups[3])

    def test_single_does_not_join_other_generation_multiturn(self):
        groups = _dispatch_conversation_groups([query(1, 0, 10), query(2, 0, 20), query(3, 1, 20)])
        self.assertIsNone(groups[1])
        self.assertEqual(groups[2], groups[3])

    def test_ambiguous_multiturn_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            _dispatch_conversation_groups([query(1), query(2), query(3, 1)])
        self.assertEqual(error.exception.status_code, 400)

    def test_manual_multiturn_preserved(self):
        self.assertEqual(_dispatch_conversation_groups([query(1), query(2, 1)]), {1: "g1", 2: "g1"})


if __name__ == "__main__":
    unittest.main()
