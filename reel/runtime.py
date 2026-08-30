# -*- coding: utf-8 -*-
"""THE DELIVERED LENGTH — one number, decided once, including the logo close.

MEASURED 2026-08-06 on every reel this pipeline has shipped:

    alfouad_run3      15.30s      nanocare_film     16.69s
    alfouad_v2_rev    11.00s      web_reel_f3815e3b  9.40s

The format is fifteen seconds — the owner named it and `--max-seconds` defaults to it — and not
one delivered file is fifteen seconds. Two separate reasons, and neither was anybody's mistake:

  1. **The end card was never in the budget.** `_scenes_for` divides `max_seconds` among the
     GENERATED scenes, then `append_endcard_to_reel` adds its card afterwards. The card is 2.6s
     cross-faded 0.5s, so every reel ships `budget + 2.1s`. The one number the owner asked for
     described a part of the film.
  2. **The beat grid may only round UP.** `tempo.quantise` snaps each cut to the next beat that
     still clears its legibility floor, which is right — a caption clipped mid-read to buy a
     rhythm is a bad trade — but nothing then reconciled the total, so the film grew. The repo's
     own log records it: "15.08s -> 16.80s".

So the budget is resolved HERE, once, and it is the length of the FILE: the scenes, the card, and
the cross-fade that joins them. `fit_to_runtime` then lands the cuts on that number instead of
merely starting from it.

WHY THE CARD GOT SHORTER. 2.6 seconds of static logo is 17% of a fifteen-second ad — the single
longest shot in the film would be the one with nothing happening in it. A commercial's end frame
holds long enough to read a name and a URL, which is under two seconds; the beat before it is what
the viewer remembers. This is a craft decision and it is reversible in one constant.
"""
from __future__ import annotations

from typing import Sequence

# The brand close. `ENDCARD_S` is how long the card itself runs; `ENDCARD_XFADE_S` is how much of
# it overlaps the last shot, so the card only ADDS the difference to the film.
ENDCARD_S = 1.8
ENDCARD_XFADE_S = 0.5

# The format. Named here so the CLI default, the API default and the budget arithmetic cannot
# drift apart — they were three separate literals.
DEFAULT_RUNTIME_S = 15.0

# One frame at 30fps. A delivered length inside this of the target IS the target; chasing anything
# finer is chasing the encoder's own rounding.
FRAME_S = 1.0 / 30.0


def endcard_tail_s(*, seconds: float = ENDCARD_S, xfade_s: float = ENDCARD_XFADE_S) -> float:
    """How much the end card ADDS to a finished reel — its length minus the overlap."""
    return max(0.0, float(seconds) - float(xfade_s))


def picture_budget_s(max_seconds: float = DEFAULT_RUNTIME_S, *, with_endcard: bool = True) -> float:
    """How long the CUT footage may run so the delivered file lands on `max_seconds`.

    A film with no card (`--no-logo`) gets the whole runtime; one that closes on the logo — the
    owner's requirement, and the default — gives the card its share up front instead of being
    surprised by it afterwards.
    """
    total = float(max_seconds or DEFAULT_RUNTIME_S)
    return max(FRAME_S, total - (endcard_tail_s() if with_endcard else 0.0))


# A natural read, and the breath between lines — the same pace `creative.py` already used for its
# per-scene floor, kept in one place so the estimate and the floor cannot disagree.
WORDS_PER_SECOND = 2.2
_BREATH_S = 0.4
_LEAD_S = 0.5

# How much of the picture a script should carry. Below this the film has beats with nothing being
# said in them; above it, the read is a rush. A commercial is not wall-to-wall narration — the
# opening and the resolve are usually music — so the floor is well under 1.0.
_MIN_SPEECH_SHARE = 0.55


def narration_fit(words: int, lines: int, picture_s: float) -> tuple[float, str]:
    """How long this script actually reads, and whether it fits the picture.

    Returns `(estimated_seconds, note)`. The note is empty when the script fits; otherwise it says
    which way it misses. NOTHING here changes a duration — that is the point. The film used to be
    shortened to whatever the copy happened to be, so a fifteen-second format shipped at 9.4s;
    a script that does not fill its format is a WRITING problem and gets said out loud instead.
    """
    w, n = max(0, int(words)), max(0, int(lines))
    if not w:
        return 0.0, ""
    speech = w / WORDS_PER_SECOND + _BREATH_S * max(0, n - 1) + _LEAD_S
    picture = float(picture_s or 0.0)
    if picture <= 0:
        return round(speech, 2), ""
    if speech > picture + 0.5:
        return round(speech, 2), (
            f"the script reads ~{speech:.1f}s over a {picture:.1f}s picture — it will be crowded; "
            f"cut about {int((speech - picture) * WORDS_PER_SECOND)} words")
    # Only when the gap is worth a rewrite. A note that says "about 1 more words would fill the
    # format" is noise, and noise is how a real warning gets ignored.
    short_by = int((picture * _MIN_SPEECH_SHARE - speech) * WORDS_PER_SECOND)
    if speech < picture * _MIN_SPEECH_SHARE and short_by >= 3:
        return round(speech, 2), (
            f"the script reads ~{speech:.1f}s over a {picture:.1f}s picture — the film carries "
            f"{picture - speech:.1f}s on sound alone; about {short_by} more words would "
            "fill the format")
    return round(speech, 2), ""


def fit_to_runtime(durations: Sequence[float], floors: Sequence[float], beat_s: float,
                   target_s: float) -> tuple[list[float], str]:
    """Cut lengths that land on the beat AND sum to exactly `target_s`.

    Returns `(durations, note)`. The note is never empty when something had to give — a film that
    could not be fitted must SAY so rather than quietly run long, which is the behaviour this
    replaces.

    PRECEDENCE, and it is the same ladder `tempo.quantise` already established:

      1. the LEGIBILITY FLOORS — a line must have time to be said and a caption time to be read;
         the owner has rejected a reel over each of these, so they are never traded away;
      2. the TOTAL — the delivered length is a promise to the platform and to the owner;
      3. the BEAT — an aesthetic contract, and the first thing to yield.

    So the slack ABOVE each floor is what gets scaled, every interior cut is snapped to the beat
    it can afford, and the final boundary is pinned to the target exactly. When the floors alone
    exceed the runtime the film is returned AT its floors with a note that says by how much: the
    copy is too long for the format, and shortening a line is a writing decision, not an
    arithmetic one.
    """
    ds = [float(d) for d in (durations or [])]
    if not ds:
        return [], "no scenes"
    fl = [float(f) for f in (floors or [])]
    fl += [0.0] * (len(ds) - len(fl))
    fl = fl[:len(ds)]
    target = float(target_s or 0.0)
    if target <= 0:
        return [round(d, 3) for d in ds], "no runtime target"

    floor_total = sum(fl)
    if floor_total > target + 1e-6:
        out = [round(max(d, f), 3) for d, f in zip(ds, fl)]
        return out, (f"the copy needs {floor_total:.2f}s and the format allows {target:.2f}s — "
                     f"shipping {sum(out):.2f}s; shorten a line to make the runtime")

    # 1) Scale only the room ABOVE the floors, so a scene pinned by its narration keeps its length
    #    and the elastic scenes absorb the whole adjustment.
    room = target - floor_total
    slack = [max(0.0, d - f) for d, f in zip(ds, fl)]
    total_slack = sum(slack)
    if total_slack > 1e-9:
        ds = [f + s * room / total_slack for f, s in zip(fl, slack)]
    else:
        ds = list(fl)
        ds[max(range(len(ds)), key=lambda i: ds[i])] += room

    # 2) Snap the interior boundaries to the beat; pin the last one to the target.
    bounds = [0.0]
    for i in range(len(ds) - 1):
        want = bounds[-1] + ds[i]
        end = want
        if beat_s and beat_s > 0:
            k = round(want / beat_s)
            end = k * beat_s
            while end < bounds[-1] + fl[i] - 1e-6 or end <= bounds[-1] + 1e-6:
                k += 1
                end = k * beat_s
        # Never eat the floors of the scenes still to come — that is what turns a rounding
        # decision into a clipped caption three cuts later. And when that cap bites, come DOWN to
        # the previous beat rather than landing wherever the arithmetic stopped: the surplus goes
        # to the scenes after it (in practice the held product resolve, which is the elastic one),
        # and the cut stays on the grid. Capping without re-snapping put the last cut of a
        # four-scene film at 12.300s on a 0.667s beat — off the grid by nearly half a beat.
        cap = target - sum(fl[i + 1:])
        if end > cap:
            end = (int((cap + 1e-9) / beat_s) * beat_s) if beat_s and beat_s > 0 else cap
        end = max(end, bounds[-1] + fl[i], bounds[-1] + FRAME_S)
        bounds.append(end)
    bounds.append(target)

    out = [round(bounds[i + 1] - bounds[i], 3) for i in range(len(ds))]
    # THE RESIDUAL GOES ON THE LAST CUT. Rounding N differences independently leaves the sum a
    # millisecond off the target — the identical drift `pacing.split_for_pace` and `tempo.quantise`
    # were both fixed for. The target is the contract, so the last shot absorbs it; the last shot
    # is also the one that can afford to, being the held product resolve.
    if len(out) > 1:
        tail = round(target - sum(out[:-1]), 3)
        if tail >= max(fl[-1], FRAME_S) - 1e-9:
            out[-1] = tail
    drift = abs(sum(out) - target)
    note = "" if drift <= 1e-3 else f"landed {sum(out):.3f}s against a {target:.3f}s target"
    return out, note
