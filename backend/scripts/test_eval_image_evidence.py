"""Image upload -> verified visual input -> multimodal judgment, offline only."""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from app.core.enums import EvalRunStatus
from app.models.ai_eval import EvalArtifact, EvalJudgment
from app.services import eval_artifacts, eval_judge, claude_runner
from app.services.generators import anthropic_http_runner
from scripts import test_eval_reliability as reliability, test_eval_artifacts as artifacts


def png():
    output = io.BytesIO()
    Image.new('RGB', (1920, 1080), 'purple').save(output, format='PNG')
    return output.getvalue()


class ImageEvidenceTests(unittest.TestCase):
    setUp = reliability.ReliabilityTests.setUp
    tearDown = reliability.ReliabilityTests.tearDown
    row = reliability.ReliabilityTests.row
    upload = artifacts.ArtifactTests.upload

    def pending(self):
        return self.row(status=EvalRunStatus.running, runner='r', claim_token='claim',
            payload=json.dumps({'prompt': '生成紫色16:9图片', 'expected': '紫色16:9图片'}))

    def test_images_reach_model_and_audit_contains_original_dimensions_and_hash(self):
        run = self.pending()
        with tempfile.TemporaryDirectory() as root, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(root)):
            uploaded = self.upload(run, '结果.png', png())['data']
            run.status = EvalRunStatus.done
            self.db.commit()
            engine = Mock()
            engine.supports_images.return_value = True
            def stream(*args, **kwargs):
                with Image.open(io.BytesIO(base64.b64decode(kwargs['images'][0]['data']))) as preview:
                    self.assertEqual(preview.size, (1600, 900))
                prompt = kwargs['prompt_builder']()
                self.assertIn('1920', prompt)
                self.assertIn(uploaded['sha256'], prompt)
                verdict = {k: {'pass': True, 'note': '核验完成'} for k in claude_runner._JUDGE_DIM_KEYS}
                verdict.update(score=5, summary='完成')
                yield {'type': 'result', 'text': json.dumps(verdict)}
            engine.stream_generate.side_effect = stream
            with patch.object(eval_judge.generators, 'get_provider', return_value=engine):
                eval_judge.judge_run(self.db, run, provider='anthropic_http')
            self.assertEqual(run.verdict, 'pass')
            audit = self.db.get(EvalJudgment, run.judgment_id).input_json
            self.assertIn('sent_to_model', audit)
            self.assertIn(uploaded['sha256'], audit)
            self.assertNotIn(base64.b64encode(png()).decode(), audit)

    def test_unreadable_or_tampered_images_never_become_visual_inputs(self):
        run = self.pending()
        with tempfile.TemporaryDirectory() as root, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(root)):
            self.upload(run, 'bad.png', b'not an image')
            good = self.upload(run, 'good.png', png())['data']
            record = self.db.get(EvalArtifact, good['artifact_id'])
            (Path(root) / record.storage_key).write_bytes(b'tampered')
            images, evidence = eval_artifacts.image_evidence(self.db, run)
            self.assertEqual(images, [])
            self.assertEqual([item['status'] for item in evidence], ['unknown', 'unknown'])

    def test_images_from_other_attempt_or_run_are_not_reused(self):
        run = self.pending()
        with tempfile.TemporaryDirectory() as root, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(root)):
            item = self.upload(run, 'old.png', png())['data']
            self.db.get(EvalArtifact, item['artifact_id']).attempt = 2
            self.db.commit()
            self.assertEqual(eval_artifacts.image_evidence(self.db, run), ([], []))
            self.assertEqual(eval_artifacts.image_evidence(self.db, self.pending()), ([], []))

    def test_file_readable_supports_real_png(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'image.png'
            path.write_bytes(png())
            parsed = eval_artifacts.inspect_file(path, path.name)
            self.assertEqual((parsed['width'], parsed['height']), (1920, 1080))

    def test_text_only_provider_does_not_claim_visual_access(self):
        run = self.pending()
        with tempfile.TemporaryDirectory() as root, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(root)):
            self.upload(run, 'image.png', png())
            run.status = EvalRunStatus.done
            self.db.commit()
            engine = Mock()
            engine.supports_images.return_value = False
            def stream(*args, **kwargs):
                self.assertNotIn('images', kwargs)
                self.assertIn('当前判定引擎未配置视觉能力', kwargs['prompt_builder']())
                verdict = {k: {'pass': None, 'note': '缺少视觉证据'} for k in claude_runner._JUDGE_DIM_KEYS}
                yield {'type': 'result', 'text': json.dumps(verdict)}
            engine.stream_generate.side_effect = stream
            with patch.object(eval_judge.generators, 'get_provider', return_value=engine):
                eval_judge.judge_run(self.db, run, provider='deepseek')
            self.assertEqual(run.verdict, 'error')
            self.assertIsNone(run.score)

    def test_http_provider_sends_image_blocks(self):
        response = Mock(status_code=400, text='test response')
        with patch.object(anthropic_http_runner, 'is_available', return_value=True), \
                patch.object(anthropic_http_runner, '_acquire_slot', return_value=True), \
                patch.object(anthropic_http_runner, '_slots'), \
                patch.object(anthropic_http_runner.requests, 'post', return_value=response) as post:
            list(anthropic_http_runner.stream_generate('judge', images=[{'mime_type': 'image/jpeg', 'data': 'abc'}]))
            blocks = post.call_args.kwargs['json']['messages'][0]['content']
            self.assertEqual(blocks[0]['type'], 'text')
            self.assertEqual(blocks[1], {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'abc'}})


if __name__ == '__main__':
    unittest.main()
