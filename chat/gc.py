"""Artifact garbage collection — mark-and-sweep over the session workdir.

Every stage writes a content-hash-named artifact (clipNN_stage_HASH.mp4) and
older renders are kept on purpose so undo/redo and cache-hits work. Over a long
session that accumulates. This sweeps artifacts that NOTHING references anymore
(not a clip's current/stage output, not any history snapshot, not an archived
variant, not a pending-plan preview, not a compilation) AND are older than a
grace period.

Defaults to dry-run: it reports what it WOULD delete; pass dry_run=False (the
API's ?force=true) to actually unlink.
"""

from __future__ import annotations

import time
from pathlib import Path


def _referenced_paths(session) -> set[str]:
    """Every artifact path still reachable from the live session state."""
    refs: set[str] = set()

    def add(p) -> None:
        if p:
            try:
                refs.add(str(Path(p).resolve()))
            except (OSError, ValueError):
                pass

    def from_clips(clips) -> None:
        for c in clips or []:
            add(c.get("current"))
            for st in c.get("stages", []):
                add(st.get("output"))

    from_clips(session.data.get("clips", []))
    from_clips(session.data.get("archived", []))
    for h in session.data.get("history", []):
        clips = h["clips"] if isinstance(h, dict) else h
        from_clips(clips)
    pp = session.data.get("pending_plan")
    if pp and isinstance(pp.get("preview"), dict):
        add(pp["preview"].get("file"))
    for cp in session.data.get("compilations", []):
        add(cp.get("file"))
    return refs


def collect(session, dry_run: bool = True, max_age_days: float = 7.0) -> dict:
    """Sweep unreferenced, aged-out artifacts from the session workdir."""
    refs = _referenced_paths(session)
    now = time.time()
    cutoff = now - max_age_days * 86400
    workdir = session.workdir

    candidates: list[Path] = []
    for pat in ("clip*.mp4", "comp*.mp4", "_comp_step*.mp4"):
        candidates.extend(workdir.glob(pat))

    removed: list[dict] = []
    kept = 0
    freed = 0
    for f in candidates:
        try:
            rp = str(f.resolve())
        except (OSError, ValueError):
            continue
        if rp in refs:
            kept += 1
            continue
        try:
            stat = f.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff:
            kept += 1  # unreferenced but still within the grace window
            continue
        removed.append({
            "name": f.name,
            "size_mb": round(stat.st_size / 1e6, 2),
            "age_days": round((now - stat.st_mtime) / 86400, 1),
        })
        freed += stat.st_size
        if not dry_run:
            f.unlink(missing_ok=True)
            # timing-stage sidecar (.map.json) lives and dies with its mp4
            side = f.with_name(f.stem + ".map.json")
            side.unlink(missing_ok=True)

    return {
        "dry_run": dry_run,
        "removed": removed,
        "removed_count": len(removed),
        "kept": kept,
        "freed_mb": round(freed / 1e6, 2),
    }
