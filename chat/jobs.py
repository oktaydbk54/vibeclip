"""In-process job queue with a single worker thread.

Why single-worker: the app has one global SESSION and the pipeline relies on
module/global state and a shared on-disk artifact cache. Serializing renders
through one worker keeps every existing assumption valid while moving the work
off the request thread — so HTTP returns instantly and the UI can stream live
progress (chat/app.py SSE) and cancel.
"""

from __future__ import annotations

import itertools
import queue
import threading

from pipeline import progress as pg


class Job:
    def __init__(self, jid: str, kind: str, label: str, fn):
        self.id = jid
        self.kind = kind          # 'chat' | 'tool'
        self.label = label
        self.fn = fn              # fn(job) -> result dict
        self.status = "queued"    # queued|running|done|error|cancelled
        self.progress = 0.0
        self.message = ""
        self.result = None
        self.error = None
        self.cancel_event = threading.Event()

    def public(self, with_result: bool = False) -> dict:
        d = {"id": self.id, "kind": self.kind, "label": self.label,
             "status": self.status, "progress": round(self.progress, 3),
             "message": self.message, "error": self.error}
        if with_result:
            d["result"] = self.result
        return d


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.q: "queue.Queue[Job]" = queue.Queue()
        self.subscribers: list["queue.Queue[dict]"] = []
        self.lock = threading.RLock()
        self._ids = itertools.count(1)
        self.current: Job | None = None
        # Called (no args) when a job finishes and the queue is empty —
        # app.py hooks auto-GC here. Must be cheap-ish and never raise.
        self.on_idle = None
        threading.Thread(target=self._loop, daemon=True).start()

    # -------------------------------------------------------- submission
    def submit(self, kind: str, label: str, fn) -> Job:
        jid = f"job{next(self._ids)}"
        job = Job(jid, kind, label, fn)
        with self.lock:
            self.jobs[jid] = job
        self.q.put(job)
        self._broadcast("job_queued", job)
        return job

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if job and job.status in ("queued", "running"):
            job.cancel_event.set()
            return True
        return False

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    # -------------------------------------------------------- SSE pub/sub
    def subscribe(self) -> "queue.Queue[dict]":
        q: "queue.Queue[dict]" = queue.Queue(maxsize=256)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _broadcast(self, etype: str, job: Job) -> None:
        evt = {"type": etype,
               "job": job.public(with_result=etype == "job_done")}
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(evt)
            except queue.Full:
                pass

    # -------------------------------------------------------- worker loop
    def _loop(self) -> None:
        while True:
            job = self.q.get()
            if job.cancel_event.is_set():
                job.status = "cancelled"
                self._broadcast("job_done", job)
                continue
            job.status = "running"
            self.current = job
            self._broadcast("job_progress", job)

            def emit(d: dict) -> None:
                if "progress" in d:
                    job.progress = d["progress"]
                if d.get("message"):
                    job.message = d["message"]
                self._broadcast("job_progress", job)

            pg.set_context(job.cancel_event, emit)
            try:
                job.result = job.fn(job)
                job.status = ("cancelled" if job.cancel_event.is_set()
                              else "done")
                job.progress = 1.0
            except pg.CancelledError:
                job.status = "cancelled"
            except Exception as e:  # noqa: BLE001 — surface to the UI
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
            finally:
                pg.clear_context()
                self.current = None
            self._broadcast("job_done", job)
            if self.q.empty() and self.on_idle is not None:
                try:
                    self.on_idle()
                except Exception:  # noqa: BLE001 — GC must never kill worker
                    pass


MANAGER = JobManager()
