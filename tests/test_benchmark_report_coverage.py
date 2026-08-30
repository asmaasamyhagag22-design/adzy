"""P-32: the benchmark headline "Avg SWOT-critical 0.88 / PASS" is a mean over only the fields that
had ground-truth labels — interpretive fields (score=None) are silently excluded. The report must
state the real denominator so 0.88-over-4-of-7 can't read as 0.88-over-all-7."""
from __future__ import annotations

from benchmark.graders import Grade, UrlGradeSet
from benchmark.report import aggregate, write_report_md


def _grade_set():
    # 4 SWOT-critical fields have GT labels (scored); 3 interpretive ones have none (score=None).
    grades = [
        Grade("name", 1.0, swot_critical=True),
        Grade("category", 1.0, swot_critical=True),
        Grade("offerings", 0.5, swot_critical=True),
        Grade("contact_phone", 1.0, swot_critical=True),
        Grade("audience_type", None, swot_critical=True),   # interpretive, no GT
        Grade("value_prop", None, swot_critical=True),
        Grade("tone", None, swot_critical=True),
    ]
    return UrlGradeSet(url_id="u1", grades=grades)


def test_coverage_counts_scored_vs_total_swot_critical():
    agg = aggregate([_grade_set()], {"u1": {"vertical": "cafe", "tier": "clean"}}, {})
    o = agg["overall"]
    assert o["swot_critical_scored"] == 4
    assert o["swot_critical_total"] == 7
    # the average is over the 4 scored fields only (0.875), NOT diluted by the 3 None fields
    assert abs(o["avg_swot_critical"] - 0.875) < 1e-6


def test_report_headline_states_the_denominator(tmp_path):
    agg = aggregate([_grade_set()], {"u1": {"vertical": "cafe", "tier": "clean"}}, {})
    path = tmp_path / "report.md"
    write_report_md([_grade_set()], agg, {"timestamp": "2026-07-17"}, str(path))
    md = path.read_text(encoding="utf-8")
    assert "4/7" in md                      # the real denominator is visible
    assert "ground-truth" in md             # and WHY the other 3 are excluded
