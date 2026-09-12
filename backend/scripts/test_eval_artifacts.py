"""Actual file upload → deterministic verification → judgment, using only an isolated DB."""
import asyncio
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import inspect, text

from app.api import eval_queue
from app.core.deps import RunnerCtx
from app.core.enums import EvalRunStatus
from app.models.ai_eval import EvalArtifact, EvalJudgment
from app.services import eval_artifacts
from app.db.migrate import ensure_eval_run_history_table
from scripts import test_eval_reliability as reliability


def workbook(formula=True, cached='3'):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="数据" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>1</v></c><c r="B1">' + ('<f>A1+2</f>' if formula else '') + (f'<v>{cached}</v>' if cached is not None else '') + '</c></row></sheetData></worksheet>')
    return output.getvalue()


class ArtifactTests(unittest.TestCase):
    setUp = reliability.ReliabilityTests.setUp
    tearDown = reliability.ReliabilityTests.tearDown
    row = reliability.ReliabilityTests.row
    judge = reliability.ReliabilityTests.judge

    def upload(self, row, name, content, token='claim'):
        return asyncio.run(eval_queue.upload_artifact(row.id, UploadFile(filename=name, file=io.BytesIO(content)),
            row.runner, token, self.db, RunnerCtx(device=None)))

    def run_with_rules(self, rules):
        return self.row(status=EvalRunStatus.running, runner='r', claim_token='claim',
                        payload=json.dumps({'verification_rules': rules, 'prompt': '生成Excel', 'expected': '含正确公式'}))

    def test_upload_actual_xlsx_validate_and_judge_with_evidence(self):
        rules = [{'kind': 'file_readable', 'file_pattern': '*.xlsx'},
                 {'kind': 'sheet_exists', 'file_pattern': '*.xlsx', 'expected': '数据'},
                 {'kind': 'cell_formula', 'file_pattern': '*.xlsx', 'sheet': '数据', 'cell': 'B1'},
                 {'kind': 'cell_value', 'file_pattern': '*.xlsx', 'sheet': '数据', 'cell': 'B1', 'expected': 3}]
        row = self.run_with_rules(rules)
        with tempfile.TemporaryDirectory() as directory, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(directory)):
            uploaded = self.upload(row, '结果.xlsx', workbook())['data']
            self.assertEqual(self.upload(row, '结果.xlsx', workbook())['data']['artifact_id'], uploaded['artifact_id'])
            report = eval_artifacts.verify_run(self.db, row)
            self.assertEqual(report['status'], 'pass')
            self.assertEqual(len(report['checks']), 4)
            row.status = EvalRunStatus.done
            self.db.commit()
            self.judge(row)
            self.assertEqual(row.verdict, 'pass')
            self.assertEqual(json.loads(row.verdict_dims)['artifact_verification']['status'], 'pass')
            self.assertIn(uploaded['sha256'], self.db.get(EvalJudgment, row.judgment_id).input_json)

    def test_actual_missing_formula_overrides_semantic_pass(self):
        row = self.run_with_rules([{'kind': 'cell_formula', 'file_pattern': '*.xlsx', 'sheet': '数据', 'cell': 'B1'}])
        with tempfile.TemporaryDirectory() as directory, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(directory)):
            self.upload(row, '结果.xlsx', workbook(formula=False))
            row.status = EvalRunStatus.done
            self.db.commit()
            self.judge(row)
            self.assertEqual(row.verdict, 'fail')
            self.assertFalse(json.loads(row.verdict_dims)['artifact_expected']['pass'])

    def test_missing_capture_is_unknown_even_if_llm_votes_pass(self):
        row = self.run_with_rules([{'kind': 'file_readable', 'file_pattern': '*.xlsx'}])
        row.status = EvalRunStatus.done
        self.db.commit()
        self.judge(row)
        self.assertEqual(row.verdict, 'error')
        self.assertIsNone(row.score)
        self.assertIsNone(json.loads(row.verdict_dims)['artifact_expected']['pass'])

    def test_missing_formula_cache_and_changed_file_are_unknown(self):
        row = self.run_with_rules([{'kind': 'cell_value', 'file_pattern': '*.xlsx', 'sheet': '数据', 'cell': 'B1', 'expected': 3}])
        with tempfile.TemporaryDirectory() as directory, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(directory)):
            self.upload(row, '结果.xlsx', workbook(cached=None))
            self.assertEqual(eval_artifacts.verify_run(self.db, row)['status'], 'unknown')
            file = self.db.query(EvalArtifact).one()
            (Path(directory) / file.storage_key).write_bytes(b'changed')
            self.assertEqual(eval_artifacts.verify_run(self.db, row)['status'], 'unknown')

    def test_stale_upload_rejected_and_old_attempt_not_reused(self):
        row = self.run_with_rules([{'kind': 'file_readable', 'file_pattern': '*.xlsx'}])
        with tempfile.TemporaryDirectory() as directory, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(directory)):
            with self.assertRaises(HTTPException) as error:
                self.upload(row, '结果.xlsx', workbook(), 'old-token')
            self.assertEqual(error.exception.status_code, 409)
            self.upload(row, '../结果.xlsx', workbook())
            self.assertEqual(self.db.query(EvalArtifact).one().name, '结果.xlsx')
            row.status = EvalRunStatus.failed
            self.db.commit()
            eval_queue.reset_conversation_for_retry(self.db, row)
            self.db.commit()
            self.assertEqual(eval_artifacts.verify_run(self.db, row)['status'], 'unknown')

    def test_corrupt_file_fails_but_unsupported_format_abstains(self):
        row = self.run_with_rules([{'kind': 'file_readable', 'file_pattern': '*'}])
        with tempfile.TemporaryDirectory() as directory, patch.object(eval_artifacts, 'ARTIFACT_ROOT', Path(directory)):
            self.upload(row, '报告.xlsx', b'broken zip')
            self.assertEqual(eval_artifacts.verify_run(self.db, row)['status'], 'fail')
            self.db.query(EvalArtifact).delete()
            self.db.commit()
            self.upload(row, '图.png', b'unsupported')
            self.assertEqual(eval_artifacts.verify_run(self.db, row)['status'], 'unknown')

    def test_migration_is_idempotent_and_keeps_existing_data(self):
        row = self.row(answer='历史结果保留')
        row_id = row.id
        self.db.close()
        with self.engine.begin() as conn:
            conn.execute(text('ALTER TABLE eval_run DROP COLUMN judgment_id'))
            conn.execute(text('ALTER TABLE eval_query DROP COLUMN verification_rules'))
        ensure_eval_run_history_table(self.engine)
        ensure_eval_run_history_table(self.engine)
        self.assertIn('judgment_id', {c['name'] for c in inspect(self.engine).get_columns('eval_run')})
        self.assertIn('verification_rules', {c['name'] for c in inspect(self.engine).get_columns('eval_query')})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT answer FROM eval_run WHERE id=:id'), {'id': row_id}).scalar(), '历史结果保留')


if __name__ == '__main__':
    unittest.main()
