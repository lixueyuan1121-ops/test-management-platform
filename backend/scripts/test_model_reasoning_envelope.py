"""Regression for observed reasoning blocks inside gateway text fields."""
import json
import unittest
from app.services.claude_runner import _parse_line
from app.services.generation_stream import consume
from app.services.requirement_analysis import collect


class ReasoningEnvelopeTests(unittest.TestCase):
    def test_reasoning_text_field_is_skipped_without_marking_real_text(self):
        state={'message_id':'m1','has_text':False}
        event={'type':'stream_event','event':{'type':'content_block_start','content_block':{'type':'text','text':[{'summary':[],'type':'reasoning'}]}}}
        self.assertIsNone(_parse_line(json.dumps(event),state))
        self.assertFalse(state['has_text'])
        snapshot={'type':'assistant','message':{'id':'m1','content':[{'type':'text','text':'[]'}]}}
        self.assertEqual(_parse_line(json.dumps(snapshot),state)['text'],'[]')

    def test_reasoning_then_valid_json_then_success_completes(self):
        events=[{'type':'delta','text':[{'summary':[],'type':'reasoning'}]},
                {'type':'delta','text':'[{"title":"test"}]'},
                {'type':'result','text':'[{"title":"test"}]'}]
        updates=[]
        raw,meta,error=consume(iter(events),updates.append)
        self.assertIsNone(error)
        self.assertEqual(json.loads(raw),[{'title':'test'}])
        self.assertFalse(any('summary' in (text or '') or 'reasoning' in (text or '') for text in updates))

    def test_analysis_path_ignores_reasoning_too(self):
        class Engine:
            def stream_generate(self,*args,**kwargs):
                yield {'type':'delta','text':[{'type':'reasoning','summary':[]}]}
                yield {'type':'result','text':'{"rules":[]}'}
        self.assertEqual(collect(Engine(),'test'),'{'+'"rules":[]'+'}')

    def test_mixed_text_and_reasoning_snapshot_and_result(self):
        blocks=[{'type':'reasoning','summary':[]},{'type':'text','text':'[]'}]
        snapshot={'type':'assistant','message':{'content':[{'type':'text','text':blocks}]}}
        self.assertEqual(_parse_line(json.dumps(snapshot))['text'],'[]')
        self.assertEqual(_parse_line(json.dumps({'type':'result','result':blocks}))['text'],'[]')

    def test_reasoning_only_without_success_is_not_accepted(self):
        raw,meta,error=consume(iter([{'type':'delta','text':[{'type':'reasoning','summary':[]}]}]))
        self.assertEqual(raw,'')
        self.assertTrue(error)

if __name__=='__main__': unittest.main()
