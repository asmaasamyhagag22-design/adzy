"""U1 objective-deduction gate — grade `build_campaign_objective` against per-URL labels.

╔══════════════════════════════════════════════════════════════════════════════════════════╗
║ ⚠  THIS ENTRYPOINT SPENDS REAL MONEY. It is NOT an offline check.                        ║
║    `python -m benchmark.grade_u1` makes ONE LIVE GEMINI CALL PER LABELLED URL (~12/run), ║
║    and `_load_env()` below loads the repo-root .env, so it has creds even when your      ║
║    shell has none. On 2026-07-15 this was misread as "offline+free": ~72 billed calls.   ║
║    Refused automatically under pytest / BASEERA_NO_SPEND=1 (see spend_tripwire.py).      ║
║    To grade for free, inject a caller:  grade_u1(caller=my_fake)                         ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝

`python -m benchmark.grade_u1` : for every benchmark URL that carries an `expected_objective` label
in benchmark/ground_truth.json, load its cached business_profile.json, run build_campaign_objective,
and grade the deduced objective + destination against the label. Prints objective / destination /
combined accuracy vs the >=80% U1 gate, and exits non-zero on FAIL.

Unlabelled URLs are SKIPPED (never fabricated). No labels yet -> the gate is N/A (fill the labels).
The deduction is a live Gemini call per labelled URL; the grading logic itself is pure + unit-tested.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, List, Optional

from media_plan import build_campaign_objective
from media_plan.schemas import Destination, MetaObjective

from .runner import BENCHMARK_DIR, find_latest_scrape_dir, load_urls

U1_GATE = 0.80   # the objective-accuracy bar the unit must clear


def _norm(s: Any) -> str:
    return str(s or "").strip().lower().replace(" ", "_")


def match_objective(deduced: Optional[MetaObjective], expected: str) -> bool:
    """A label may be the API value ('OUTCOME_SALES'), the enum name ('SALES'), or the label
    ('Sales') — all match. None (no deduction) never matches."""
    if deduced is None:
        return False
    e = _norm(expected)
    return e in (_norm(deduced.value), _norm(deduced.name), _norm(deduced.label))


def match_destination(deduced: Optional[Destination], expected: str) -> bool:
    if deduced is None:
        return False
    return _norm(expected) == _norm(deduced.value)


@dataclass
class U1Grade:
    url_id: str
    expected_objective: str
    expected_destination: Optional[str]
    deduced_objective: Optional[str]
    deduced_destination: Optional[str]
    objective_match: bool
    destination_match: Optional[bool]   # None when the URL has no expected_destination label
    grounded: bool
    error: Optional[str] = None


def _labels() -> dict:
    """Per-URL entries that carry a non-null expected_objective — the only ones we grade."""
    gt = json.loads((BENCHMARK_DIR / "ground_truth.json").read_text(encoding="utf-8"))
    return {k: v for k, v in gt.items()
            if not k.startswith("_") and isinstance(v, dict) and v.get("expected_objective")}


def _scrape_json(url: str, filename: str) -> Optional[dict]:
    scrape_dir = find_latest_scrape_dir(url)
    if scrape_dir is None:
        return None
    path = scrape_dir / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _profile_for(url: str) -> Optional[dict]:
    return _scrape_json(url, "business_profile.json")


def _manifest_for(url: str) -> Optional[dict]:
    """The scrape manifest (optional) — carries link-level surfaces the profile doesn't."""
    return _scrape_json(url, "manifest.json")


def grade_u1(caller: Any = None) -> List[U1Grade]:
    """Deduce + grade U1 for every labelled URL. `caller` is injectable for tests; None -> the
    shared Gemini caller (live). A URL with no cached profile or a failed build is a miss with a
    recorded reason, never a silent pass."""
    urls = {u["id"]: u for u in load_urls().get("urls", [])}
    grades: List[U1Grade] = []
    for url_id, lab in _labels().items():
        exp_obj = str(lab.get("expected_objective"))
        exp_dest = lab.get("expected_destination")
        meta = urls.get(url_id)
        profile = _profile_for(meta["url"]) if meta else None
        if profile is None:
            grades.append(U1Grade(url_id, exp_obj, exp_dest, None, None, False, None, False,
                                  "no cached profile (scrape+extract this URL first)"))
            continue
        try:
            obj = build_campaign_objective(profile, caller=caller,
                                           manifest=_manifest_for(meta["url"]))
        except Exception as e:  # noqa: BLE001
            grades.append(U1Grade(url_id, exp_obj, exp_dest, None, None, False, None, False,
                                  f"build_campaign_objective raised: {type(e).__name__}"))
            continue
        d_obj = obj.objective if obj else None
        d_dest = obj.destination if obj else None
        grades.append(U1Grade(
            url_id=url_id, expected_objective=exp_obj, expected_destination=exp_dest,
            deduced_objective=(d_obj.value if d_obj else None),
            deduced_destination=(d_dest.value if d_dest else None),
            objective_match=match_objective(d_obj, exp_obj),
            destination_match=(match_destination(d_dest, exp_dest) if exp_dest else None),
            grounded=bool(obj and obj.is_grounded),
            error=(None if obj else "build returned None (no caller / thin profile / ungrounded)"),
        ))
    return grades


def summarize(grades: List[U1Grade]) -> dict:
    """Objective / destination / combined accuracy. destination is over URLs WITH a destination
    label; combined (objective AND destination) is over URLs with BOTH labels."""
    n = len(grades)
    obj_acc = (sum(1 for g in grades if g.objective_match) / n) if n else None
    dest = [g for g in grades if g.destination_match is not None]
    dest_acc = (sum(1 for g in dest if g.destination_match) / len(dest)) if dest else None
    both = [g for g in grades if g.destination_match is not None]
    comb_acc = (sum(1 for g in both if g.objective_match and g.destination_match) / len(both)) if both else None
    return {
        "graded": n,
        "objective_accuracy": obj_acc,
        "destination_accuracy": dest_acc,
        "destination_labeled": len(dest),
        "combined_accuracy": comb_acc,
        "gate": U1_GATE,
        "passed": (obj_acc is not None and obj_acc >= U1_GATE),
    }


def _fmt(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _load_env() -> None:
    """Entrypoint-only: load repo-root .env so default_caller sees the Gemini creds (same idiom as
    every other __main__ in the repo). Library imports of this module never touch the environment."""
    try:
        from dotenv import load_dotenv
        load_dotenv(BENCHMARK_DIR.parent / ".env", override=False)
    except ImportError:
        pass


def main() -> int:
    # A comment is not a control — refuse mechanically before _load_env() can hand us credentials.
    from spend_tripwire import refuse_if_no_spend
    refuse_if_no_spend("one live Gemini call per labelled URL — ~12 calls per run",
                       alternative="To grade for free, inject a caller: grade_u1(caller=my_fake).")
    _load_env()
    grades = grade_u1()
    if not grades:
        print("U1 gate: N/A — no expected_objective labels in benchmark/ground_truth.json yet.")
        print("Fill `expected_objective` (+ `expected_destination`) per URL, then re-run.")
        return 0

    print("\n--- U1 objective deduction ---")
    print(f"{'url':16} {'expected':16} {'deduced':16} obj  dest  grounded  note")
    for g in grades:
        obj = "OK" if g.objective_match else "X"
        dest = "-" if g.destination_match is None else ("OK" if g.destination_match else "X")
        note = g.error or ""
        print(f"{g.url_id:16} {g.expected_objective:16.16} {str(g.deduced_objective):16.16} "
              f"{obj:4} {dest:5} {'yes' if g.grounded else 'no':8}  {note}")

    s = summarize(grades)
    print("\n============================================================")
    print(f"objective accuracy : {_fmt(s['objective_accuracy'])}  ({s['graded']} labelled URLs)")
    print(f"destination accuracy: {_fmt(s['destination_accuracy'])}  ({s['destination_labeled']} with a dest label)")
    print(f"combined accuracy   : {_fmt(s['combined_accuracy'])}")
    print(f"U1 GATE (objective >= {U1_GATE:.0%}): {'PASS' if s['passed'] else 'FAIL'}")
    return 0 if s["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
