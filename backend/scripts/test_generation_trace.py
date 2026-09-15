"""Isolated trace and stream regressions: no real provider or user payload."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from app.services import generation_trace as trace
from app.services.generation_stream import consume


class GenerationTraceTests(unittest.TestCase):
    def test_context_reaches_parallel_batches_and_does_not_leak(self):
        records=[]
        with patch.object(trace.logger, 'info', side_effect=lambda line: records.append(json.loads(line))):
            with trace.scope(job_id=17, provider='claude'):
                def work(index):
                    with trace.scope(batch_id=str(index)):
                        trace.emit('probe')
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(trace.bind(work), range(3)))
            trace.emit('outside')
        self.assertEqual({r['job_id'] for r in records[:-1]}, {17})
        self.assertEqual({r['batch_id'] for r in records[:-1]}, {'0','1','2'})
        self.assertNotIn('job_id',records[-1])

    def test_sensitive_payloads_are_not_written(self):
        records=[]
        with patch.object(trace.logger, 'info', side_effect=records.append):
            trace.emit('test',job_id=17,prompt='secret prompt',text='private model output',api_key='secret-key',url='https://secret',output_chars=42,effort='medium',structured_output=False)
        line=records[0]
        self.assertNotIn('secret',line)
        self.assertNotIn('private',line)
        self.assertEqual(json.loads(line)['output_chars'],42)
        self.assertEqual(json.loads(line)['effort'],'medium')
        self.assertFalse(json.loads(line)['structured_output'])

    def test_timeout_result_is_not_case_text(self):
        updates=[]
        raw,meta,error=consume(iter([
            {'type':'delta','text':'API Error: Stream idle timeout - no chunks received'},
            {'type':'result','is_error':True,'text':'API Error: Stream idle timeout - no chunks received','error':'API Error: Stream idle timeout - no chunks received'},
        ]),updates.append)
        self.assertEqual(raw,'')
        self.assertEqual(updates[-1],'')
        self.assertEqual(trace.error_code(error),'upstream_stream_idle_timeout')

    def test_error_preserves_real_partial_body(self):
        raw,meta,error=consume(iter([
            {'type':'delta','text':'[{"title":"partial"'},
            {'type':'result','is_error':True,'text':'API Error: timeout','error':'timeout'},
        ]))
        self.assertEqual(raw,'[{"title":"partial"')
        self.assertTrue(error)

    def test_list_blocks_and_reset_are_supported(self):
        raw,meta,error=consume(iter([
            {'type':'delta','text':'outdated'},
            {'type':'delta','reset':True,'text':[{'type':'text','text':'[]'}]},
            {'type':'result','text':['[]']},
        ]))
        self.assertEqual(raw,'[]')
        self.assertIsNone(error)

    def test_missing_completion_is_not_accepted(self):
        raw,meta,error=consume(iter([{'type':'delta','text':'[]'}]))
        self.assertTrue(error)

    def test_model_end_is_logged_on_error_and_consumer_close(self):
        records=[]
        @trace.model_call
        def model():
            yield {'type':'heartbeat'}
            yield {'type':'error','msg':'Stream idle timeout'}
        with patch.object(trace.logger,'info',side_effect=lambda line: records.append(json.loads(line))):
            with trace.scope(job_id=17):
                list(model())
                stream=model();next(stream);stream.close()
        ends=[r for r in records if r['event']=='model_call_end']
        self.assertEqual([r['status'] for r in ends],['failed','interrupted'])
        self.assertTrue(all(r['job_id']==17 for r in ends))
        self.assertEqual(len({r['call_id'] for r in ends}),2)

    def test_cli_error_still_logs_transport_process_and_call_end(self):
        from unittest.mock import Mock
        from app.services import claude_runner, requirement_analysis
        records=[]
        proc=Mock()
        proc.pid=123
        proc.stdout=iter([json.dumps({'type':'result','is_error':True,'result':'API Error: Stream idle timeout','errors':['Stream idle timeout']})+'\n'])
        proc.poll.return_value=0
        with patch.object(trace.logger,'info',side_effect=lambda line: records.append(json.loads(line))), \
             patch.object(claude_runner,'is_available',return_value=True), \
             patch.object(claude_runner,'_claude_env',return_value={}), \
             patch.object(claude_runner,'_acquire_slot',return_value=True), \
             patch.object(claude_runner,'_slots',Mock()), \
             patch.object(claude_runner.subprocess,'Popen',return_value=proc):
            with trace.scope(job_id=17,batch_id='batch-0'):
                raw,meta,error=consume(claude_runner.stream_generate('',prompt_builder=lambda:'synthetic'))
        self.assertEqual(raw,'')
        self.assertTrue(error)
        process=next(r for r in records if r['event']=='model_process_end')
        self.assertEqual(process['transport_lines'],1)
        self.assertEqual(process['output_chars'],0)
        self.assertTrue(process['got_result'])
        self.assertEqual(process['job_id'],17)
        self.assertEqual(process['batch_id'],'batch-0')
        self.assertEqual(next(r for r in records if r['event']=='model_call_end')['status'],'failed')

    def test_logger_failure_does_not_break_generation(self):
        with patch.object(trace.logger,'info',side_effect=OSError('disk full')):
            trace.emit('test',job_id=17)
            self.assertEqual(consume(iter([{'type':'result','text':'[]'}]))[0],'[]')

if __name__=='__main__': unittest.main()
