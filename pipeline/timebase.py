"""Frame-accurate time base: rational fps, timecode, frame snapping.

Clips are stored in float seconds everywhere; this only converts at the edges —
formatting a timecode for display, snapping an edit boundary to the frame grid,
parsing a timecode the user typed. NTSC rates ("30000/1001" = 29.97) are kept
rational so a minute of footage lands on the right frame.

Timecode is non-drop (frames labelled 0..nominal-1 where nominal = round(fps));
for 29.97 this drifts from wall-clock, which is the correct non-drop behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Timebase:
    num: int = 30
    den: int = 1

    @classmethod
    def from_rate(cls, rate: str | float | int) -> "Timebase":
        """Parse ffprobe's r_frame_rate ('30000/1001', '30', 25.0)."""
        if isinstance(rate, str) and "/" in rate:
            n, d = rate.split("/")
            num, den = int(float(n)), int(float(d))
            if den == 0:
                num, den = 30, 1
            return cls(num, den)
        f = float(rate or 0)
        if f <= 0:
            return cls(30, 1)
        # common NTSC values back to rationals
        for n, d in ((30000, 1001), (24000, 1001), (60000, 1001), (24, 1),
                     (25, 1), (30, 1), (50, 1), (60, 1)):
            if abs(f - n / d) < 0.01:
                return cls(n, d)
        return cls(int(round(f * 1000)), 1000)

    @property
    def fps(self) -> float:
        return self.num / self.den

    @property
    def nominal(self) -> int:
        """Whole frames-per-second used for timecode labelling (30 for 29.97)."""
        return max(1, int(round(self.fps)))

    def to_frames(self, seconds: float) -> int:
        return int(round(max(0.0, seconds) * self.fps))

    def frame_dur(self) -> float:
        return self.den / self.num

    def snap_s(self, seconds: float) -> float:
        """Snap a time to the nearest frame boundary (for edit edges)."""
        return self.to_frames(seconds) / self.fps

    def nudge_s(self, seconds: float, frames: int) -> float:
        """Move a time by +/- N frames, snapped to the grid, clamped at 0."""
        return max(0.0, (self.to_frames(seconds) + frames) / self.fps)

    def tc(self, seconds: float) -> str:
        """Format seconds as HH:MM:SS:FF (non-drop)."""
        tf = self.to_frames(seconds)
        nf = self.nominal
        ff = tf % nf
        rest = tf // nf
        ss = rest % 60
        mm = (rest // 60) % 60
        hh = rest // 3600
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    def parse_tc(self, tc: str) -> float:
        """Parse 'HH:MM:SS:FF' (or 'MM:SS:FF', 'SS:FF') back to seconds."""
        parts = [int(p) for p in str(tc).strip().replace(";", ":").split(":")]
        while len(parts) < 4:
            parts.insert(0, 0)
        hh, mm, ss, ff = parts[-4], parts[-3], parts[-2], parts[-1]
        total_frames = ((hh * 60 + mm) * 60 + ss) * self.nominal + ff
        return total_frames / self.fps
