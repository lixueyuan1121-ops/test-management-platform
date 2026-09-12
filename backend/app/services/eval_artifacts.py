"""Deterministic checks on captured files; never execute document code or fetch URLs."""
import fnmatch
import hashlib
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field, model_validator

from app.models.ai_eval import EvalArtifact
from app.services.eval_judgment_store import attempt_of
from app.services.eval_snapshot import payload_of

ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / 'storage' / 'eval_artifacts'
MAX_BYTES = 20 * 1024 * 1024


class ArtifactRule(BaseModel):
    kind: Literal['file_readable', 'text_contains', 'sheet_exists', 'cell_formula', 'cell_value', 'min_pages']
    file_pattern: str = Field(min_length=1, max_length=255)
    expected: str | int | float | None = None
    sheet: str | None = Field(None, max_length=255)
    cell: str | None = Field(None, pattern=r'^[A-Z]{1,3}[1-9][0-9]{0,6}$')

    @model_validator(mode='after')
    def required_fields(self):
        if self.kind in ('text_contains', 'sheet_exists', 'cell_value', 'min_pages') and self.expected is None:
            raise ValueError('该检查项需要填写期望值')
        if self.kind in ('cell_formula', 'cell_value') and (not self.sheet or not self.cell):
            raise ValueError('单元格检查需要 Sheet 名称和单元格地址')
        if self.kind == 'min_pages' and (isinstance(self.expected, bool) or not str(self.expected).isdigit() or int(self.expected) < 1):
            raise ValueError('最少页数须为正整数')
        return self


def rules_json(rules):
    return json.dumps([rule.model_dump() for rule in rules], ensure_ascii=False) if rules else None


def inspect_file(path, name):
    suffix = Path(name).suffix.lower()
    if suffix in ('.txt', '.md', '.csv', '.tsv', '.json', '.html'):
        try:
            text = path.read_text(encoding='utf-8-sig')
        except UnicodeError:
            raise NotImplementedError('文本编码不是UTF-8，需要转换后核验')
        if suffix == '.json':
            json.loads(text)
        return {'text': text, 'format': suffix}
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > 200:
            raise NotImplementedError("PDF加密或超过200页核验上限")
        return {'pages': len(reader.pages), 'text': '\n'.join(p.extract_text() or '' for p in reader.pages), 'format': suffix}
    if suffix not in ('.xlsx', '.docx', '.pptx'):
        raise NotImplementedError(f'暂不支持核验 {suffix or "无扩展名"} 文件')
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 64 * 1024 * 1024:
            raise NotImplementedError('解压内容超过64MB核验上限')
        def xml(name):
            return ET.fromstring(archive.read(name))
        xml("[Content_Types].xml")
        xml("_rels/.rels")
        if suffix == '.docx':
            root = xml('word/document.xml')
            return {'text': '\n'.join(n.text or '' for n in root.iter() if n.tag.endswith('}t')), 'format': suffix}
        if suffix == '.pptx':
            names = sorted(n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n))
            if not names:
                raise ValueError('PPTX 没有幻灯片')
            return {'pages': len(names), 'text': '\n'.join(n.text or '' for name in names for n in xml(name).iter() if n.tag.endswith('}t')), 'format': suffix}
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        relations = {n.attrib['Id']: n.attrib['Target'] for n in xml('xl/_rels/workbook.xml.rels')}
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(t.text or '' for t in n.findall('.//s:t', ns)) for n in xml('xl/sharedStrings.xml')]
        sheets = {}
        for sheet in xml('xl/workbook.xml').findall('s:sheets/s:sheet', ns):
            rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = relations[rid]
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            cells = {}
            for cell in xml(member).findall('.//s:c', ns):
                value, formula = cell.find('s:v', ns), cell.find('s:f', ns)
                text = value.text if value is not None else None
                if cell.attrib.get('t') == 's' and text is not None:
                    text = shared[int(text)]
                if cell.attrib.get('t') == 'inlineStr':
                    text = ''.join(t.text or '' for t in cell.findall('.//s:t', ns))
                cells[cell.attrib['r']] = {'value': text, 'formula': formula.text if formula is not None else None}
            sheets[sheet.attrib['name']] = cells
        if not sheets:
            raise ValueError('XLSX 没有工作表')
        return {'sheets': sheets, 'text': '\n'.join(str(c['value']) for sheet in sheets.values() for c in sheet.values() if c['value'] is not None), 'format': suffix}


def check(rule, artifact):
    if rule.kind == 'file_readable':
        return True, '文件格式解析成功'
    if rule.kind == 'text_contains':
        found = str(rule.expected) in artifact['text']
        return found, f'文本包含「{rule.expected}」：{found}'
    if rule.kind == 'min_pages':
        if 'pages' not in artifact:
            raise NotImplementedError('该文件格式没有可靠页数；Word需渲染后核验')
        return artifact['pages'] >= int(rule.expected), f"实际页数 {artifact['pages']}，要求至少 {rule.expected}"
    if 'sheets' not in artifact:
        return False, '要求 Excel 工作簿，实际产物不是 XLSX'
    if rule.kind == 'sheet_exists':
        return str(rule.expected) in artifact['sheets'], f"实际 Sheet：{', '.join(artifact['sheets'])}"
    cell = artifact['sheets'].get(rule.sheet, {}).get(rule.cell)
    if cell is None:
        return False, f'不存在 {rule.sheet}!{rule.cell}'
    if rule.kind == 'cell_formula':
        return bool(cell['formula']), f"{rule.sheet}!{rule.cell} 公式：{cell['formula'] or '无'}"
    if cell['formula'] and cell['value'] is None:
        raise NotImplementedError('公式没有缓存计算结果，需要客户端计算保存后核验')
    actual = cell['value']
    try:
        from decimal import Decimal
        equal = Decimal(str(actual)) == Decimal(str(rule.expected))
    except Exception:
        equal = str(actual) == str(rule.expected)
    return equal, f'{rule.sheet}!{rule.cell} 实际值 {actual}，期望 {rule.expected}'


def verify_run(db, run):
    raw = payload_of(run).get('verification_rules') or []
    if not raw:
        return None
    rules = [ArtifactRule.model_validate(r) for r in raw]
    from pypdf.errors import PdfReadError
    files = db.query(EvalArtifact).filter_by(eval_run_id=run.id, attempt=attempt_of(db, run)).all()
    parsed = {}
    results = []
    for index, rule in enumerate(rules):
        matching = [f for f in files if fnmatch.fnmatchcase(f.name, rule.file_pattern)]
        if not matching:
            results.append({'rule': index + 1, 'status': 'unknown', 'reason': f'未采集到匹配 {rule.file_pattern} 的实际文件'})
            continue
        for file in matching:
            item = {'rule': index + 1, 'artifact_id': file.id, 'name': file.name, 'sha256': file.sha256}
            try:
                path = ARTIFACT_ROOT / file.storage_key
                if not path.is_file():
                    raise NotImplementedError('已登记的产物文件缺失，需重新采集')
                if path.resolve().parent != ARTIFACT_ROOT.resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != file.sha256:
                    raise NotImplementedError('产物文件完整性校验失败，需重新采集')
                if file.id not in parsed:
                    parsed[file.id] = inspect_file(path, file.name)
                passed, reason = check(rule, parsed[file.id])
                item.update(status='pass' if passed else 'fail', reason=reason)
            except NotImplementedError as error:
                item.update(status='unknown', reason=str(error))
            except (ValueError, KeyError, IndexError, OSError, zipfile.BadZipFile, ET.ParseError, PdfReadError) as error:
                item.update(status='fail', reason=f'产物格式损坏或无法解析：{type(error).__name__}')
            results.append(item)
    status = 'fail' if any(r['status'] == 'fail' for r in results) else ('unknown' if any(r['status'] == 'unknown' for r in results) else 'pass')
    return {'status': status, 'checks': results, 'evidence': [
        {'artifact_id': file.id, 'name': file.name, 'sha256': file.sha256,
         'text_excerpt': parsed.get(file.id, {}).get('text', '')[:12000]} for file in files]}
