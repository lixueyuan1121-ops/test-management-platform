"""综合评价在线短链(匿名只读):把测评任务的 AI 综合评价 HTML 片段渲染成一张独立网页。

- 短链码(summary_share_code)在综合评价 done 时由 ensure_share_code 生成一次并稳定复用;
  对外 URL = PLATFORM_BASE_URL + /r/<code>(见 notify/pipeline 推链)。
- GET /r/<code> 无鉴权(匿名可达,便于推推群内直接点开):只暴露已消毒的 summary_html
  (_sanitize_html 已去除脚本/事件属性),不泄露任何其它任务数据;码是 8 字节随机 hex,不可枚举。
- 短链只在综合评价 done 时可访问;running/failed/未生成 → 友好提示页(非 500)。

注册在 SPA catch-all 之前(api_router 早于 _mount_frontend),故 /r/<code> 不会被前端路由吞掉。
"""
import html as _html
import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.models.ai_eval import EvalTask

# 独立 prefix(不挂 /api):短链要短、要像页面 URL。
router = APIRouter(tags=["eval-report"])


def ensure_share_code(db: Session, task: EvalTask) -> str:
    """给任务分配稳定的综合评价短链码(已有则复用)。调用方负责 commit。

    码 = 8 字节随机 hex(16 字符,列宽 16),几乎不可能碰撞;真撞了重取。
    """
    if task.summary_share_code:
        return task.summary_share_code
    for _ in range(5):
        code = secrets.token_hex(8)
        exists = (db.query(EvalTask.id)
                  .filter(EvalTask.summary_share_code == code).first())
        if not exists:
            task.summary_share_code = code
            return code
    # 极端连撞:退化用更长的码(仍落 16 列宽内的前缀不可行 → 直接用 token_hex(8),接受一次重试后放弃)
    task.summary_share_code = secrets.token_hex(8)
    return task.summary_share_code


def share_path(code: str) -> str:
    return f"/r/{code}"


_PAGE_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ margin:0; background:#f5f7fa; color:#34495e; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:28px 20px 60px; }}
  .card {{ background:#fff; border:1px solid #e4e7ed; border-radius:10px; padding:26px 30px; box-shadow:0 1px 3px rgba(0,0,0,.04); }}
  .hd {{ margin-bottom:18px; padding-bottom:14px; border-bottom:1px solid #eef2f6; }}
  .hd h1 {{ font-size:20px; margin:0 0 6px; color:#1f2d3d; }}
  .hd .meta {{ font-size:12px; color:#8a97a6; }}
  .body {{ line-height:1.75; font-size:14px; }}
  .body h2 {{ font-size:17px; margin:18px 0 10px; color:#1f2d3d; border-left:3px solid #00b386; padding-left:10px; }}
  .body h3 {{ font-size:14px; margin:12px 0 6px; color:#34495e; }}
  .body table {{ border-collapse:collapse; width:100%; margin:10px 0; }}
  .body th, .body td {{ border:1px solid #dfe6ec; padding:7px 11px; text-align:left; font-size:13px; }}
  .body th {{ background:#f3f8f7; color:#1f2d3d; }}
  .body ul, .body ol {{ padding-left:24px; margin:8px 0; }}
  .body blockquote {{ border-left:3px solid #dfe6ec; margin:8px 0; padding:6px 14px; color:#7d8a9b; background:#f8fafc; }}
  .body code {{ background:#eef2f6; border-radius:3px; padding:1px 5px; font-size:13px; }}
  .ft {{ text-align:center; color:#b4bcc7; font-size:12px; margin-top:22px; }}
  .tip {{ text-align:center; color:#8a97a6; padding:60px 20px; font-size:15px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="hd">
      <h1>{title}</h1>
      <div class="meta">{meta}</div>
    </div>
    <div class="body">{content}</div>
  </div>
  <div class="ft">AI 对话测评 · 综合评价报告</div>
</div>
</body>
</html>"""


def render_report_page(task: EvalTask) -> str:
    """把任务的 summary_html(已消毒片段)包成一张完整网页。summary 未就绪 → 提示页。"""
    title = _html.escape(f"测评综合评价 · {task.name or ''}".strip(" ·"))
    if task.summary_status != "done" or not task.summary_html:
        tip = {
            "running": "综合评价正在生成中，请稍后刷新…",
            "failed": "综合评价生成失败，请到平台重新生成。",
        }.get(task.summary_status, "该测评任务尚未生成综合评价。")
        content = f'<div class="tip">{_html.escape(tip)}</div>'
        meta = ""
    else:
        content = task.summary_html  # 已经过 _sanitize_html,可安全内联
        at = task.summary_at.isoformat(sep=" ", timespec="seconds") if task.summary_at else ""
        meta = _html.escape(f"生成时间 {at} · 引擎 {task.summary_provider or ''}".strip(" ·"))
    return _PAGE_TMPL.format(title=title, meta=meta, content=content)


@router.get("/r/{code}", response_class=HTMLResponse, include_in_schema=False)
def view_shared_report(code: str):
    """匿名查看综合评价在线报告。码无效/评价未就绪都返回 HTML 提示页(不 500、不泄露)。"""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        task = (db.query(EvalTask)
                .filter(EvalTask.summary_share_code == code).first()) if code else None
        if not task:
            return HTMLResponse(
                _PAGE_TMPL.format(title="报告不存在", meta="",
                                  content='<div class="tip">链接无效或报告已被删除。</div>'),
                status_code=404)
        return HTMLResponse(render_report_page(task))
    finally:
        db.close()
