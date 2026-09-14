"""Regression coverage for model text envelopes; no network calls."""
import unittest
from app.services.requirement_analysis import collect
from app.services.requirement_output import OutputError


class Engine:
    def __init__(self, events):
        self.events = events
    def stream_generate(self, *args, **kwargs):
        yield from self.events


class TextEventTests(unittest.TestCase):
    def run_events(self, *events):
        return collect(Engine(events), 'test')

    def test_list_delta_and_final_blocks(self):
        events = [
            {'type': 'delta', 'text': [{'type': 'text', 'text': '{"ok":'}]},
            {'type': 'delta', 'text': ['true', '}']},
            {'type': 'result', 'text': [{'type': 'text', 'text': '{"ok":true}'}]},
        ]
        progress = []
        self.assertEqual(collect(Engine(events), 'test', on_progress=progress.append), '{"ok":true}')
        self.assertTrue(all(isinstance(x, str) for x in progress))

    def test_reset_blocks(self):
        self.assertEqual(self.run_events(
            {'type': 'delta', 'text': 'obsolete'},
            {'type': 'delta', 'reset': True, 'text': [{'type': 'text', 'text': '{}'}]},
            {'type': 'result'}), '{}')

    def test_thinking_and_tools_not_forwarded(self):
        self.assertEqual(self.run_events({'type': 'result', 'text': [
            {'type': 'thinking', 'thinking': 'private'},
            {'type': 'tool_use', 'input': {'text': 'wrong'}},
            {'type': 'output_text', 'text': '{}'}]}), '{}')

    def test_unknown_blocks_preserve_previous_text(self):
        with self.assertRaises(OutputError) as error:
            self.run_events({'type': 'delta', 'text': '{"ok":'},
                            {'type': 'delta', 'text': [{'rules': []}]})
        self.assertEqual(error.exception.code, 'unsupported_text')
        self.assertEqual(error.exception.raw, '{"ok":')

    def test_scalar_rejected_as_recoverable_error(self):
        with self.assertRaises(OutputError) as error:
            self.run_events({'type': 'result', 'text': 123})
        self.assertEqual(error.exception.code, 'unsupported_text')

    def test_empty_list_result_keeps_deltas(self):
        self.assertEqual(self.run_events({'type': 'delta', 'text': '{}'},
                                        {'type': 'result', 'text': []}), '{}')

    def test_no_completion_still_fails(self):
        with self.assertRaises(OutputError) as error:
            self.run_events({'type': 'delta', 'text': ['{}']})
        self.assertEqual(error.exception.code, 'interrupted')

    def test_provider_error_not_accepted_as_text(self):
        with self.assertRaises(OutputError) as error:
            self.run_events({'type': 'result', 'is_error': True, 'error': 'server failed', 'text': ['{}']})
        self.assertEqual(error.exception.code, 'provider_error')


if __name__ == '__main__':
    unittest.main()
