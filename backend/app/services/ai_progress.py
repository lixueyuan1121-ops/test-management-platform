"""Best-effort, bounded progress snapshots for durable AI jobs.

While running, AiJob.result contains only {progress: ...}; run_job replaces it
with the validated final result. Failed jobs retain the preview. No migration or
connection held across model waits, and progress must never fail generation.
"""
import json
import logging
import threading
import time

from sqlalchemy import update

from app.models import AiJob
from app.services import generation_trace as trace

logger = logging.getLogger("test_platform")


def progress_of(raw):
    try:
        result = json.loads(raw or "{}")
        return result.get("progress") if isinstance(result, dict) else None
    except (ValueError, TypeError):
        return None


def _tail(text, limit):
    return text.encode("utf-8")[-limit:].decode("utf-8", "ignore") if limit else ""


class JobProgress:
    interval = 1.0

    def __init__(self, factory, job_id):
        self.factory, self.job_id = factory, job_id
        self.lock = threading.Lock()
        self.units = {}
        self.stage = "preparing"
        self.started_at = int(time.time() * 1000)
        self.last_output_at = None
        self.last_save = -float("inf")

    def phase(self, stage):
        with self.lock:
            trace.emit("phase_changed", job_id=self.job_id, stage=stage)
            self.stage = stage
            self._save(force=True)

    def flush(self):
        with self.lock:
            self._save(force=True)

    def unit(self, key, title=None, status=None, raw=None, note=None, attempt=None):
        with self.lock:
            unit = self.units.setdefault(str(key), {"id": str(key), "title": (title or str(key))[:120],
                                                     "status": "pending", "text": "", "chars": 0,
                                                     "received_chars": 0, "attempt_chars": 0, "attempt": 1})
            changed_attempt = attempt is not None and attempt > unit["attempt"]
            if changed_attempt:
                unit["attempt"] = attempt
                unit["attempt_chars"] = 0
                trace.emit("unit_retry", job_id=self.job_id, batch_id=str(key), attempt=attempt)
            changed_status = status and status != unit["status"]
            first_text = raw and not unit["chars"]
            if status:
                unit["status"] = status
            if note is not None:
                unit["note"] = note[:500]
            if raw is not None:
                # A final snapshot/normalization can replace text with a shorter version.
                # Count each attempt's high-water mark separately from current preview length.
                unit["received_chars"] += max(0, len(raw) - unit["attempt_chars"])
                unit["attempt_chars"] = max(unit["attempt_chars"], len(raw))
                unit["chars"] = len(raw)
                unit["text"] = _tail(raw, 12000)
                unit["truncated"] = len(unit["text"]) < len(raw)
                self.last_output_at = int(time.time() * 1000)
            if changed_status or first_text:
                trace.emit("unit_progress", job_id=self.job_id, batch_id=str(key), stage=self.stage, status=unit["status"], output_chars=unit["chars"], elapsed_ms=int(time.time()*1000)-self.started_at)
            self._save(force=bool(changed_status or first_text or changed_attempt))

    def callback(self, key):
        # raw=None is a worker heartbeat, not invented model output.
        return lambda raw=None: self.unit(key, raw=raw)

    def _save(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_save < self.interval:
            return
        self.last_save = now
        units = list(self.units.values())
        snapshot = {"stage": self.stage, "started_at": self.started_at,
                    "updated_at": int(time.time() * 1000), "last_output_at": self.last_output_at,
                    "total": len(units), "completed": sum(u["status"] in ("done", "failed", "warning") for u in units),
                    "chars": sum(u["chars"] for u in units),
                    "received_chars": sum(u["received_chars"] for u in units)}
        # Metadata + escaped text must fit MySQL 5.6 TEXT (65535 bytes).
        # Preserve all counts, and prefer active/recent output in the preview.
        ranked = sorted(enumerate(units), key=lambda pair: (pair[1]["status"] == "running", pair[0]), reverse=True)[:100]
        selected = [u for _, u in sorted(ranked)]
        budget = 30000
        texts = {}
        for u in sorted(selected, key=lambda u: (u["status"] == "running", units.index(u)), reverse=True):
            text = _tail(u["text"], min(12000, budget))
            texts[u["id"]] = text
            budget -= len(json.dumps(text, ensure_ascii=False).encode("utf-8"))
            budget = max(0, budget)
        snapshot["units"] = [{**u, "text": texts[u["id"]], "truncated": len(texts[u["id"]]) < u["chars"]} for u in selected]
        snapshot["omitted"] = len(units) - len(selected)
        raw = json.dumps({"progress": snapshot}, ensure_ascii=False)
        while len(raw.encode("utf-8")) > 60000 and snapshot["units"]:
            discard = next((i for i, u in enumerate(snapshot["units"]) if u["status"] != "running"), 0)
            snapshot["units"].pop(discard)
            snapshot["omitted"] += 1
            raw = json.dumps({"progress": snapshot}, ensure_ascii=False)
        try:
            with self.factory() as db:
                db.execute(update(AiJob).where(AiJob.id == self.job_id, AiJob.status == "running").values(result=raw))
                db.commit()
        except Exception:
            # No model text in logs. Subsequent chunks/phase boundaries retry.
            trace.emit("progress_save_failed", job_id=self.job_id)
            logger.warning("AI progress snapshot unavailable job=%s", self.job_id)
