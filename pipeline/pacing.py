"""V4.3 — Pattern-interrupt scheduler (the "retention brain").

Research finding: top shorts hold attention with a meaningful on-screen change
every 2-5 seconds — and the interval must be JITTERED (a fixed cadence gets
tuned out). This module finds static spans longer than `max_static` between
existing interrupts (zooms, sfx, b-roll, fx hits) and schedules new ones at
deliberately uneven intervals, snapped to word starts so they land on speech.

Deterministic by design (no RNG): the jitter comes from cycling an uneven
interval pattern, so replays/undo are reproducible.
"""

from __future__ import annotations

# Deliberately uneven (seconds). Cycling these = jitter without randomness.
JITTER_INTERVALS = [3.2, 4.4, 2.7, 3.8]
MAX_STATIC_DEFAULT = 5.0
MIN_SPACING = 1.8       # never closer than this to an existing interrupt
HOOK_GUARD = 1.0        # leave the very first beat alone

# What the scheduler may insert, cycled in order. The variety IS the point:
# repeating one interrupt type gets tuned out like a fixed interval does.
KIND_CYCLE = ["zoom", "whoosh", "shake", "ding"]


def _snap_to_word(t: float, words: list[dict], window: float = 0.7) -> float:
    """Move t to the nearest word START within `window` (cuts land on speech)."""
    best, best_d = t, window
    for w in words:
        d = abs(w["start"] - t)
        if d < best_d:
            best, best_d = w["start"], d
    return best


def plan_interrupts(words: list[dict], existing: list[float],
                    duration: float,
                    max_static: float = MAX_STATIC_DEFAULT) -> list[float]:
    """Return new interrupt times filling every static span > max_static."""
    anchors = sorted({0.0, duration,
                      *(t for t in existing if 0.0 <= t <= duration)})
    out: list[float] = []
    ic = 0
    for a, b in zip(anchors, anchors[1:]):
        cursor = max(a, HOOK_GUARD)
        while b - cursor > max_static:
            t = cursor + JITTER_INTERVALS[ic % len(JITTER_INTERVALS)]
            ic += 1
            if b - t < MIN_SPACING:
                # interval overshoots the next anchor — split the span
                # instead of leaving it static (midpoint is always valid
                # here since b - cursor > max_static >= 2*MIN_SPACING).
                t = cursor + (b - cursor) / 2
            t = _snap_to_word(t, words)
            if t - cursor < MIN_SPACING or b - t < MIN_SPACING:
                t = cursor + max(MIN_SPACING, (b - cursor) / 2)
            out.append(round(t, 2))
            cursor = t
    return sorted(out)


def longest_static_span(existing: list[float], duration: float) -> float:
    anchors = sorted({0.0, duration,
                      *(t for t in existing if 0.0 <= t <= duration)})
    return max((b - a for a, b in zip(anchors, anchors[1:])), default=duration)
