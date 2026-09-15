import json
import unittest
from unittest.mock import Mock, patch
from app.services import claude_runner as cli, requirement_analysis as analysis
from app.services.requirement_output import OutputError

ERROR = 'API Error: Stream idle timeout - no chunks received'

def api_error():
    return {'type':'assistant','isApiErrorMessage':True,'error':'unknown',
            'message':{'content':[{'type':'text','text':ERROR}]}}

class ApiErrorMessageTests(unittest.TestCase):
    def test_real_cli_error_envelope_is_not_text(self):
        event=cli._parse_line(json.dumps(api_error()),{})
        self.assertEqual(event,{'type':'error','msg':ERROR})

    def test_analysis_error_preserves_business_output_only(self):
        class Engine:
            def __init__(self, events):self.events=events
            def stream_generate(self,*a,**kw):yield from self.events
        for prefix,expected in [('', ''),(ERROR,''),('{"summary":"actual partial','{"summary":"actual partial')]:
            with self.subTest(prefix=prefix):
                updates=[]
                events=([{'type':'delta','text':prefix}] if prefix else [])+[cli._parse_line(json.dumps(api_error()),{})]
                with self.assertRaises(OutputError) as caught:
                    analysis.collect(Engine(events),'synthetic',on_progress=updates.append)
                self.assertEqual(caught.exception.code,'provider_timeout')
                self.assertEqual(caught.exception.raw,expected)
                self.assertNotIn(ERROR,updates)

    def test_flagged_delta_is_rejected_before_accumulation(self):
        class Engine:
            def stream_generate(self,*a,**kw):yield {'type':'delta','is_error':True,'text':ERROR,'error':ERROR}
        with self.assertRaises(OutputError) as caught:analysis.collect(Engine(),'synthetic')
        self.assertEqual(caught.exception.raw,'')

    def test_cli_stops_at_error_and_releases_process(self):
        proc=Mock();proc.stdout=iter([json.dumps(api_error())+'\n']);proc.poll.return_value=None
        slot=Mock()
        with patch.object(cli,'is_available',return_value=True),patch.object(cli,'_claude_env',return_value={}),patch.object(cli,'_acquire_slot',return_value=True),patch.object(cli,'_slots',slot),patch.object(cli.subprocess,'Popen',return_value=proc):
            events=list(cli.stream_generate('',prompt_builder=lambda:'synthetic'))
        self.assertEqual(events,[{'type':'error','msg':ERROR}])
        proc.kill.assert_called_once();slot.release.assert_called_once()
