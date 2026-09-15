import asyncio
import json
import threading
import unittest
from unittest.mock import Mock, patch

import httpx
from app.core.config import settings
from app.services import claude_messages as adapter, claude_runner as cli
from app.services.generation_stream import consume
from app.services.requirement_analysis import collect
from app.services.requirement_output import OutputError


def events(text='{"ok":true}', reason='end_turn'):
    return [{'type':'message_start','message':{}},
            {'type':'content_block_delta','delta':{'type':'text_delta','text':text}},
            {'type':'message_delta','delta':{'stop_reason':reason},'usage':{'output_tokens':7}},
            {'type':'message_stop'}]


def sse(rows):
    return ''.join('event: '+str(row['type'])+'\ndata: '+json.dumps(row,ensure_ascii=False)+'\n\n' for row in rows).encode()


class MessagesTests(unittest.TestCase):
    def setUp(self):
        self.patches=[]
        for name,value in {'AI_CLAUDE_TRANSPORT':'messages','AI_ENABLED':True,
             'CLAUDE_MESSAGES_BASE_URL':'http://127.0.0.1:15721',
             'CLAUDE_MESSAGES_API_KEY':'synthetic-secret','CLAUDE_MESSAGES_MODEL':'synthetic-model'}.items():
            item=patch.object(settings,name,value);item.start();self.patches.append(item)
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])

    def response(self,rows,*,status=200,content_type='text/event-stream',body=None):
        real=httpx.AsyncClient
        self.requests=[]
        def handle(request):
            self.requests.append(request)
            return httpx.Response(status,headers={'content-type':content_type,'request-id':'fixture-request'},
                                  content=body if body is not None else sse(rows))
        transport=httpx.MockTransport(handle)
        return patch.object(adapter.httpx,'AsyncClient',side_effect=lambda **kw: real(transport=transport,**kw))

    def test_complete_stream_has_no_tools_and_uses_explicit_connection(self):
        with self.response(events()),patch.object(cli.subprocess,'Popen') as process:
            raw,meta,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture'))
        self.assertEqual(raw,'{"ok":true}');self.assertIsNone(error)
        self.assertTrue(meta['complete']);self.assertEqual(meta['output_tokens'],7)
        process.assert_not_called();self.assertEqual(len(self.requests),1)
        request=self.requests[0];body=json.loads(request.content)
        self.assertEqual(str(request.url),'http://127.0.0.1:15721/v1/messages')
        self.assertEqual(request.headers['x-api-key'],'synthetic-secret')
        self.assertEqual(body['model'],'synthetic-model')
        self.assertNotIn('tools',body);self.assertNotIn('thinking',body)
        self.assertFalse(cli.supports_structured_output());self.assertFalse(cli.supports_effort())

    def test_reasoning_blocks_never_become_visible_text(self):
        rows=events('正文')
        rows.insert(1,{'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'private'}})
        rows.insert(2,{'type':'content_block_start','content_block':{'type':'tool_use','input':{'secret':'private'}}})
        with self.response(rows):raw,meta,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture'))
        self.assertEqual(raw,'正文');self.assertIsNone(error)

    def test_stream_requires_stop_and_preserves_partial_output(self):
        with self.response(events('partial')[:-1]):raw,meta,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture'))
        self.assertEqual(raw,'partial');self.assertTrue(error)
        self.assertEqual(len(self.requests),1) # no hidden retries

    def test_token_limit_cannot_be_accepted_as_successful_json(self):
        with self.response(events(reason='max_tokens')),self.assertRaises(OutputError) as caught:
            collect(cli,'fixture')
        self.assertEqual(caught.exception.code,'truncated')

    def test_http_and_stream_errors_do_not_echo_body_or_credentials(self):
        for status,content_type,rows,body in [(401,'application/json',[],b'synthetic-secret'),
             (200,'text/html',[],b'synthetic-secret'),
             (200,'text/event-stream',[{'type':'error','error':{'message':'synthetic-secret'}}],None)]:
            with self.subTest(status=status,content_type=content_type),self.response(rows,status=status,content_type=content_type,body=body):
                raw,meta,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture'))
                self.assertEqual(raw,'');self.assertTrue(error);self.assertNotIn('synthetic-secret',error)

    def test_sse_multiline_unicode_and_keepalive(self):
        chunks=[]
        for row in events('中文\n文本'):
            chunks.append(': ping\n\n'+''.join('data: '+line+'\n' for line in json.dumps(row,ensure_ascii=False,indent=2).splitlines())+'\n')
        with self.response([],body=''.join(chunks).encode()):raw,meta,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture'))
        self.assertEqual(raw,'中文\n文本');self.assertIsNone(error)

    def test_empty_tool_and_malformed_endings_fail(self):
        for rows in [events(''),events(reason='tool_use'),events()[1:],events()[:1]+events()]:
            with self.subTest(rows=rows),self.response(rows):
                _,_,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture'))
                self.assertTrue(error)

    def test_cancellation_closes_request_and_releases_slot(self):
        closed=threading.Event();slot=Mock()
        async def receive(body,publish,timeout):
            try:
                publish({'type':'delta','text':'partial'})
                await asyncio.sleep(30)
            finally:closed.set()
        with patch.object(adapter,'_receive',side_effect=receive),patch.object(cli,'_slots',slot),patch.object(cli,'_acquire_slot',return_value=True):
            stream=cli.stream_generate('',prompt_builder=lambda:'fixture')
            self.assertEqual(next(stream)['text'],'partial');stream.close()
            self.assertTrue(closed.wait(2))
            # Give the owning thread its finalizer, without waiting on a remote service.
            for _ in range(100):
                if slot.release.called:break
                threading.Event().wait(0.01)
            slot.release.assert_called_once()

    def test_deadline_cancels_receive_without_retry(self):
        closed=threading.Event()
        async def receive(*a):
            try:await asyncio.sleep(30)
            finally:closed.set()
        with patch.object(adapter,'_receive',side_effect=receive) as call:
            _,_,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture',timeout=0.03))
        self.assertTrue(error);self.assertTrue(closed.wait(2));self.assertEqual(call.call_count,1)

    def test_only_empty_transient_failure_retries_once(self):
        for partial,expected_calls in [(False,2),(True,1)]:
            calls=[]
            async def receive(body,publish,timeout):
                calls.append(timeout)
                if partial:publish({'type':'delta','text':'partial'})
                if len(calls)==1:raise httpx.ReadTimeout('synthetic')
                publish({'type':'delta','text':'{}'})
                publish({'type':'result','text':'{}','complete':True,'finish_reason':'stop'})
            with self.subTest(partial=partial),patch.object(adapter,'_receive',side_effect=receive):
                raw,meta,error=consume(cli.stream_generate('',prompt_builder=lambda:'fixture',timeout=5))
            self.assertEqual(len(calls),expected_calls)
            if partial:self.assertEqual(raw,'partial');self.assertTrue(error)
            else:self.assertIsNone(error);self.assertLess(calls[1],calls[0])

    def test_image_payload_and_endpoint_suffix(self):
        with patch.object(settings,'CLAUDE_MESSAGES_BASE_URL','https://fixture.invalid/v1'):
            self.assertEqual(adapter._endpoint(),'https://fixture.invalid/v1/messages')
        body=adapter._request('fixture','system',[{'mime_type':'image/png','data':'fixture'}],{'type':'object'})
        self.assertEqual(body['messages'][0]['content'][1]['source']['data'],'fixture')
        self.assertIn('JSON Schema',body['messages'][0]['content'][0]['text'])

    def test_direct_script_still_uses_existing_validator(self):
        script=[{'action':'click','target':{'key':'fixtureKey'}},{'action':'assert_visible','target':{'key':'fixtureKey'}}]
        with patch.object(cli,'build_script_prompt',return_value='fixture'),self.response(events(json.dumps(script))), \
             patch.object(cli,'_registered_keys',return_value={'fixtureKey'}):
            result,error=cli.generate_script('gui','fixture','','')
        self.assertIsNone(error)
        self.assertEqual(result,[{**step,'args':{},'desc':''} for step in script])

if __name__=='__main__':unittest.main()
