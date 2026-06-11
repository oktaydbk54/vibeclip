"""Source ↔ output time map (Pro Faz 6 core).

A clip's timing stages (cut → jumpcut → trim) each keep a set of intervals of
their input and compact them into a shorter output. A TimeMap is the composed
result: a piecewise-linear (slope-1) mapping from SOURCE seconds to the clip's
CURRENT output seconds, represented as kept segments [(in_a, in_b, out_a)].

Where the intervals come from: each timing stage writes a `.map.json` sidecar
next to its hash-named artifact at render time (see session._run_stage) — the
ONLY moment the resolved intervals exist (trim re-anchors text against the
input transcript; aggressive filler drops come from an LLM). Sidecars follow
the same cache discipline as artifacts: same hash name = same map.

This module is pure math + JSON; it never renders or transcribes.
"""

from __future__ import annotations

import json
from pathlib import Path

Seg = tuple[float, float, float]  # (in_start, in_end, out_start)


class TimeMap:
    """Piecewise slope-1 monotonic map between two time domains."""

    def __init__(self, segments: list[Seg]):
        self.segments: list[Seg] = sorted(
            [(float(a), float(b), float(o)) for a, b, o in segments
             if b - a > 1e-9])

    # ------------------------------------------------------------- factory
    @classmethod
    def identity(cls, duration: float) -> "TimeMap":
        return cls([(0.0, duration, 0.0)])

    @classmethod
    def from_kept(cls, kept: list[tuple[float, float]]) -> "TimeMap":
        """Map input-time → compacted output-time from keep-intervals."""
        segs: list[Seg] = []
        out = 0.0
        for s, e in sorted((float(s), float(e)) for s, e in kept):
            if e - s <= 1e-9:
                continue
            segs.append((s, e, out))
            out += e - s
        return cls(segs)

    # ------------------------------------------------------------- queries
    @property
    def in_span(self) -> tuple[float, float]:
        return ((self.segments[0][0], self.segments[-1][1])
                if self.segments else (0.0, 0.0))

    @property
    def out_duration(self) -> float:
        if not self.segments:
            return 0.0
        a, b, o = self.segments[-1]
        return o + (b - a)

    def to_output(self, t: float) -> float | None:
        """Input time → output time; None if t falls in a removed span."""
        for a, b, o in self.segments:
            if a <= t < b or (t == b and b == self.segments[-1][1]):
                return o + (t - a)
        return None

    def to_input(self, t: float) -> float | None:
        """Output time → input time; None if t is past the end."""
        for a, b, o in self.segments:
            if o <= t < o + (b - a) or (
                    t == o + (b - a) and b == self.segments[-1][1]):
                return a + (t - o)
        return None

    def removed_spans(self) -> list[tuple[float, float]]:
        """Input spans that were cut out BETWEEN kept segments (gaps)."""
        gaps = []
        for (_, b1, _), (a2, _, _) in zip(self.segments, self.segments[1:]):
            if a2 - b1 > 1e-9:
                gaps.append((b1, a2))
        return gaps

    def kept_spans(self) -> list[tuple[float, float]]:
        return [(a, b) for a, b, _ in self.segments]

    # ------------------------------------------------------------- algebra
    def compose(self, nxt: "TimeMap") -> "TimeMap":
        """self: A→B, nxt: B→C  ⇒  A→C."""
        segs: list[Seg] = []
        for a, b, o in self.segments:          # B-interval [o, o + b-a)
            for na, nb, no in nxt.segments:    # B-interval [na, nb)
                lo = max(o, na)
                hi = min(o + (b - a), nb)
                if hi - lo > 1e-9:
                    segs.append((a + (lo - o), a + (hi - o),
                                 no + (lo - na)))
        return TimeMap(segs)

    # ------------------------------------------------------------- io
    def to_json(self) -> dict:
        return {"segments": [[a, b, o] for a, b, o in self.segments]}

    @classmethod
    def from_json(cls, data: dict) -> "TimeMap":
        return cls([tuple(s) for s in data["segments"]])


# ----------------------------------------------------------------- sidecars
def sidecar_path(artifact: str) -> Path:
    """`clip01_jumpcut_ab12cd34.mp4` → `clip01_jumpcut_ab12cd34.map.json`."""
    p = Path(artifact)
    return p.with_name(p.stem + ".map.json")


def write_sidecar(artifact: str, kept: list[tuple[float, float]]) -> None:
    """Persist a timing stage's resolved keep-intervals (stage-input local)."""
    sidecar_path(artifact).write_text(
        json.dumps({"kept": [[round(s, 4), round(e, 4)] for s, e in kept]}))


def read_sidecar(artifact: str) -> list[tuple[float, float]] | None:
    p = sidecar_path(artifact)
    if not p.exists():
        return None
    try:
        return [tuple(k) for k in json.loads(p.read_text())["kept"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
