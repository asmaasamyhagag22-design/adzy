"""§5 — the G2 seed-set gate. BLOCKING: Veo never fires unless the full current seed set
passes. The PASS conjunction is computed IN CODE from the boolean fields (the model never
self-passes — the same law as every other gate in this repo). Targeted regen <=3, then
RuntimeError with the verdict attached. Never silently ship drift (§9).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, Callable, List, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class _Offender(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame: int
    reason: str


class G2Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    same_person: bool
    identity_offenders: List[_Offender] = Field(default_factory=list)
    same_world_grade: bool
    grade_offenders: List[_Offender] = Field(default_factory=list)
    junk_screens: List[int] = Field(default_factory=list)
    arc_readable: bool
    verdict: str = "FAIL"


_G2_PROMPT = """You are a harsh QA gate for an ad reel's seed frames. You receive (A) a
reference sheet of the intended protagonist and (B) a numbered contact sheet
of {n} candidate frames.

Judge STRICTLY. When unsure about identity, answer false. Output strict JSON
only:

- same_person: true ONLY if EVERY frame clearly shows the person from the
  reference: same face, same hijab color AND pattern, same build.
- identity_offenders: [{{"frame": int, "reason": str}}] for every frame that fails.
- same_world_grade: palette/grade follows a deliberate act progression;
  random palette jumps = false. grade_offenders likewise.
- junk_screens: frame numbers showing ANY AI-generated "unreal" tell —
  (a) a screen/monitor with glowing abstract or sci-fi UI, holographic panels,
  or gibberish presented as readable text; OR (b) glowing neon / cyan / blue
  light strips, luminous holographic partitions, or other sci-fi glow that makes
  a real-world office look artificially futuristic. FLAG the frame EVEN IF the
  glow could be read as architectural or ambient lighting; when in doubt about a
  glow, FLAG it. A plain, real monitor showing ordinary readable text is NOT junk.
- arc_readable: could a stranger order these frames into
  tension -> effort -> payoff?
- verdict: "PASS" iff same_person && same_world_grade && junk_screens == []
  && arc_readable, else "FAIL"."""


def build_contact_sheet(seed_paths: List["str | Path"],
                        out_path: Optional["str | Path"] = None) -> bytes:
    """All seeds on ONE numbered grid (3 columns) — the G2 input. Returns PNG bytes."""
    from PIL import Image, ImageDraw
    imgs = [Image.open(p).convert("RGB") for p in seed_paths]
    w = min(i.width for i in imgs)
    imgs = [i.resize((w, int(i.height * w / i.width))) for i in imgs]
    h = min(i.height for i in imgs)
    imgs = [i.crop((0, 0, w, h)) for i in imgs]
    cols = 3
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w + (cols + 1) * 8, rows * h + (rows + 1) * 8),
                      (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(imgs):
        x = 8 + (i % cols) * (w + 8)
        y = 8 + (i // cols) * (h + 8)
        sheet.paste(im, (x, y))
        d.rectangle([x + 4, y + 4, x + 46, y + 30], fill=(255, 255, 255))
        d.text((x + 14, y + 8), str(i + 1), fill=(0, 0, 0))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    data = buf.getvalue()
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(data)
    return data


def g2_check(sheet_png: bytes, ref_sheet_png: bytes, *, n: int,
             caller: Any) -> G2Verdict:
    """One gate call. The verdict field is RE-COMPUTED in code from the booleans —
    an optimistic model cannot self-pass."""
    resp, _u = caller(
        _G2_PROMPT.format(n=n),
        "Image A is the reference sheet; image B is the numbered contact sheet. "
        "Return the structured verdict.",
        G2Verdict, group_name="render3_g2",
        images=[(ref_sheet_png, "image/png"), (sheet_png, "image/png")])
    computed = ("PASS" if (resp.same_person and resp.same_world_grade
                           and not resp.junk_screens and resp.arc_readable) else "FAIL")
    return resp.model_copy(update={"verdict": computed})


def run_g2_loop(seed_paths: List[str], ref_sheet_png: bytes, *,
                regen: Callable[[int], str], caller: Any,
                max_retries: int = 3, log=logger.info) -> G2Verdict:
    """The §5 loop: gate -> targeted regen of offender frames -> re-gate; <=`max_retries`
    rounds, then RuntimeError (Veo blocked). `regen(k)` regenerates 0-BASED seed k with the
    same refs + locked blocks and returns the new path. Mutates seed_paths in place."""
    verdict: Optional[G2Verdict] = None
    for attempt in range(max_retries):
        sheet = build_contact_sheet(seed_paths)
        verdict = g2_check(sheet, ref_sheet_png, n=len(seed_paths), caller=caller)
        if verdict.verdict == "PASS":
            log(f"[g2] PASS on attempt {attempt + 1}")
            return verdict
        bad = ({o.frame for o in verdict.identity_offenders}
               | {o.frame for o in verdict.grade_offenders}
               | set(verdict.junk_screens))
        bad = {b for b in bad if 1 <= b <= len(seed_paths)}
        if not bad:
            # M4: arc/grade FAILED but the gate named NO frame. Re-rolling seeds can't fix a
            # story-ARC/ordering problem — the shots and locked blocks are identical, so a new
            # pixel roll yields the same unreadable sequence. That is a treatment-level fix
            # (Director/G1), not a seed re-roll. Fail closed now instead of burning image spend
            # on `max_retries` blind full regens that cannot converge.
            raise RuntimeError(
                "G2 FAIL — arc/grade unreadable with no per-frame offender named. This is a "
                "treatment-level problem (shot order / story readability), not a seed the loop "
                "can re-roll; revise the treatment (Director/G1) rather than regenerating. "
                f"Veo blocked. Verdict: {verdict.model_dump()}")
        log(f"[g2] FAIL attempt {attempt + 1}: regenerating frame(s) {sorted(bad)}")
        for k in sorted(bad):
            seed_paths[k - 1] = regen(k - 1)
    raise RuntimeError(f"G2 failed {max_retries}x — Veo blocked: "
                       f"{verdict.model_dump() if verdict else 'no verdict'}")
