# -*- coding: utf-8 -*-
"""اسأل الأرشيف · Ask the archive (dashboard/archive_qa.py) — corpus assembly, retrieval fallback,
and the GROUNDING contract on the answer. Hermetic: the LLM caller and embeddings are injected, so
no network / no model / no embeddings run. The point under test is the moat applied to Q&A:
answers are drawn only from the archive, cite their source, and refuse when it isn't covered."""
from __future__ import annotations

import json

from dashboard import archive_qa as aq


def _seed(tmp_path):
    """Write a minimal but real-shaped archive (result + media plan) for a slug."""
    (tmp_path / "brand_result.json").write_text(json.dumps({
        "profile": {
            "name": {"value": "NTI", "evidence": [{"page_url": "https://nti.example/"}]},
            "description": {"value": "A telecom training institute in Egypt."},
            "category": {"value": "education"},
            "audience_type": {"value": "fresh graduates"},
            "value_propositions": [
                {"value": "Bridges the talent-industry gap",
                 "evidence": [{"page_url": "https://nti.example/careers"}]}],
            "offerings": [{"name": "Career Development Program",
                           "short_description": "3-month upskilling course"}],
            "trust_signals": [
                {"value": "Supreme Council of Universities accreditation",
                 "evidence": [{"page_url": "https://nti.example/about"}]}],
            "final_url": "https://nti.example/",
        },
        "swot": {"strengths": [{"text": "Bilingual site (ar+en)"}],
                 "weaknesses": [{"text": "No visible pricing"}],
                 "opportunities": [], "threats": []},
        "competitors": [{"candidate": {"name": "Computer Skills Co"},
                         "selection": {"name": "Computer Skills Co", "why_selected": "same category",
                                       "peer_fit_score": 0.72, "website": "https://csc.example/"}}],
        "tows": {"strategies": [{"title": "Leverage: workforce-ready graduates",
                                 "description": "Push the job-placement proof."}],
                 "priority_actions": [{"action": "Publish course prices"}]},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "brand_media_plan.json").write_text(json.dumps({
        "objectives": [{"objective": "OUTCOME_LEADS", "destination": "lead_form",
                        "funnel_stage": "bofu", "rationale": "Capture graduate applications."}],
        "base_geo": {"mode": "national"},
    }, ensure_ascii=False), encoding="utf-8")


def _corpus(tmp_path):
    return aq.build_archive_corpus("brand", out_dir=str(tmp_path), scrapes_dir=str(tmp_path / "no"))


def test_corpus_covers_every_source_with_provenance(tmp_path):
    _seed(tmp_path)
    corpus = _corpus(tmp_path)
    labels = {c.label.split(":")[0].split(" · ")[0] for c in corpus}
    assert {"Brand profile", "Value proposition", "Trust signal", "SWOT",
            "Competitor", "Strategy (TOWS)", "Priority action", "Media plan"} <= labels
    # provenance URL survives onto the chunks that have one
    trust = next(c for c in corpus if c.label == "Trust signal")
    assert trust.url == "https://nti.example/about"
    comp = next(c for c in corpus if c.label.startswith("Competitor"))
    assert comp.url == "https://csc.example/" and "same category" in comp.text


def test_keyword_retrieval_surfaces_the_relevant_chunk(tmp_path):
    _seed(tmp_path)
    corpus = _corpus(tmp_path)
    idxs = aq._retrieve("what accreditation does it have?", corpus, 3, embed_fn=None)
    picked = [corpus[i].text for i in idxs]
    assert any("accreditation" in t.lower() for t in picked)


def _caller(answer, cited, grounded):
    def _c(system, user, model, group_name=""):
        assert "ONLY the numbered EXCERPTS" in system      # the grounding contract is sent
        assert "EXCERPTS:" in user                         # excerpts are actually supplied
        return model(answer=answer, cited=cited, grounded=grounded), None
    return _c


def test_answer_is_grounded_and_maps_citations(tmp_path):
    _seed(tmp_path)
    ans = aq.answer_question("brand", "what accreditation?", out_dir=str(tmp_path),
                             scrapes_dir=str(tmp_path / "no"), embed_fn=None,
                             caller=_caller("Supreme Council of Universities accreditation.",
                                            [1], True))
    assert ans.grounded is True and ans.answer.startswith("Supreme Council")
    assert ans.citations and ans.citations[0]["n"] == 1 and ans.citations[0]["label"]
    assert ans.corpus_size > 0


def test_answer_without_citation_is_downgraded(tmp_path):
    # honesty guard: a model that claims grounded=true but cites nothing must NOT imply provenance.
    _seed(tmp_path)
    ans = aq.answer_question("brand", "anything", out_dir=str(tmp_path),
                             scrapes_dir=str(tmp_path / "no"), embed_fn=None,
                             caller=_caller("some answer", [], True))
    assert ans.grounded is False and ans.citations == []


def test_answer_refuses_when_not_in_archive(tmp_path):
    _seed(tmp_path)
    ans = aq.answer_question("brand", "what is their CEO's home address?", out_dir=str(tmp_path),
                             scrapes_dir=str(tmp_path / "no"), embed_fn=None,
                             caller=_caller("الأرشيف ما فيهوش إجابة عن ده", [], False))
    assert ans.grounded is False
    assert "الأرشيف" in ans.answer


def test_no_archive_and_empty_question_are_handled(tmp_path):
    empty = aq.answer_question("nobody", "hi", out_dir=str(tmp_path),
                               scrapes_dir=str(tmp_path / "no"), caller=_caller("x", [1], True))
    assert empty.grounded is False and empty.corpus_size == 0 and "no archive" in empty.note
    _seed(tmp_path)
    blank = aq.answer_question("brand", "   ", out_dir=str(tmp_path),
                               scrapes_dir=str(tmp_path / "no"), caller=_caller("x", [1], True))
    assert blank.answer == "" and blank.note == "empty question"


def test_embedding_path_used_when_available_then_degrades(tmp_path):
    # an embed_fn that returns usable vectors drives ranking; a broken one degrades to keywords.
    _seed(tmp_path)
    corpus = _corpus(tmp_path)

    def good_embed(texts):
        # deterministic 1-D vectors: score by presence of the word "accreditation"
        return [[1.0 if "accreditation" in t.lower() else 0.0] for t in texts]

    idxs = aq._retrieve("accreditation", corpus, 2, embed_fn=good_embed)
    assert idxs and "accreditation" in corpus[idxs[0]].text.lower()

    def broken_embed(texts):
        raise RuntimeError("no credentials")

    idxs2 = aq._retrieve("accreditation", corpus, 2, embed_fn=broken_embed)   # must not raise
    assert any("accreditation" in corpus[i].text.lower() for i in idxs2)


# --- review-fix regressions -------------------------------------------------------------

def test_answer_model_defaults_grounded_false():
    # fail-open fix: an omitted `grounded` key must NOT read as grounded (the moat).
    M = aq._build_answer_model()
    assert M.model_validate({"answer": "x", "cited": [1]}).grounded is False


def test_chunk_blanks_non_http_provenance_url():
    # XSS fix: a javascript:/data: URL from scraped data never survives onto a citation.
    assert aq.Chunk("t", "L", "javascript:alert(document.cookie)").url == ""
    assert aq.Chunk("t", "L", "data:text/html,<script>x</script>").url == ""
    assert aq.Chunk("t", "L", "  HTTPS://ok.example/x  ").url == "HTTPS://ok.example/x"
    assert aq.Chunk("t", "L", "https://ok.example/").url == "https://ok.example/"


def test_answer_supported_gates_grounding_but_not_crosslingual():
    supported = aq._answer_supported
    assert supported("Supreme Council accreditation", ["the Supreme Council accreditation"]) is True
    assert supported("founded 1985 by royal decree", ["pricing: 1000 EGP for the program"]) is False
    # cross-lingual (Arabic answer over English excerpt) shares no tokens -> NOT downgraded
    assert supported("المعهد حاصل على اعتماد رسمي", ["accreditation by the Supreme Council"]) is True
    assert supported("", ["x"]) is True and supported("y", []) is True


def test_grounded_downgraded_when_answer_unsupported(tmp_path):
    # injection/fabrication guard: model self-reports grounded + cites a REAL excerpt, but the
    # answer shares nothing with that excerpt (same language) -> support check downgrades it.
    def corpus(slug, **kw):
        return [aq.Chunk("the advanced program registration fee is 1000 EGP", "Offering: X"),
                aq.Chunk("locations: Smart Village, Giza governorate", "Locations & contact")]
    ans = aq.answer_question("b", "when was it founded?", embed_fn=None, build_corpus=corpus,
                             caller=_caller("It was founded in 1985 by royal decree.", [2], True))
    assert ans.grounded is False and ans.answer.startswith("It was founded")


def test_supported_answer_stays_grounded(tmp_path):
    def corpus(slug, **kw):
        return [aq.Chunk("the advanced professional program registration fee is 1000 EGP", "Offering")]
    ans = aq.answer_question("b", "what is the fee?", embed_fn=None, build_corpus=corpus,
                             caller=_caller("The advanced professional program registration fee is 1000 EGP.",
                                            [1], True))
    assert ans.grounded is True


def test_corpus_keeps_distinct_pages_that_share_an_intro(tmp_path):
    # de-dup fix: two pages with an identical 160+char intro but divergent bodies must BOTH survive.
    d = tmp_path / "brand_20260101"
    d.mkdir()
    intro = "National Telecommunication Institute. Home Courses About Contact NTI. " * 3
    (d / "manifest.json").write_text(json.dumps({"pages": [
        {"url": "https://b/a", "cleaned_main_text": intro + " Network Security diploma, six months."},
        {"url": "https://b/b", "cleaned_main_text": intro + " Fiber Optics diploma, four months."},
    ]}, ensure_ascii=False), encoding="utf-8")
    corpus = aq.build_archive_corpus("brand", out_dir=str(tmp_path), scrapes_dir=str(tmp_path))
    blob = " || ".join(c.text for c in corpus)
    assert "Network Security" in blob and "Fiber Optics" in blob


def test_media_plan_keeps_rationale_that_contains_the_word_None(tmp_path):
    (tmp_path / "b_media_plan.json").write_text(json.dumps({"objectives": [
        {"objective": "OUTCOME_AWARENESS",
         "rationale": "None of the current ad channels convert well"}]}), encoding="utf-8")
    corpus = aq.build_archive_corpus("b", out_dir=str(tmp_path), scrapes_dir=str(tmp_path / "no"))
    mp_chunk = next(c for c in corpus if c.label.startswith("Media plan"))
    assert "None of the current ad channels" in mp_chunk.text


def test_answer_question_never_raises_on_malformed_artifacts(tmp_path):
    # "Never raises" contract: wrong-shape (but valid) JSON must degrade, not throw.
    (tmp_path / "b_result.json").write_text(json.dumps(
        {"swot": ["x"], "tows": ["y"], "profile": "just text", "competitors": {}}), encoding="utf-8")
    (tmp_path / "b_media_plan.json").write_text(json.dumps(
        {"base_geo": ["z"], "objectives": ["q"]}), encoding="utf-8")
    corpus = aq.build_archive_corpus("b", out_dir=str(tmp_path), scrapes_dir=str(tmp_path / "no"))
    assert isinstance(corpus, list)                       # no AttributeError
    ans = aq.answer_question("b", "hi", out_dir=str(tmp_path), scrapes_dir=str(tmp_path / "no"),
                             caller=_caller("x", [1], True))
    assert ans.grounded is False                          # empty corpus -> honest refusal, no crash


def test_top_level_array_result_does_not_crash(tmp_path):
    (tmp_path / "b_result.json").write_text("[{\"x\": 1}]", encoding="utf-8")   # array, not object
    corpus = aq.build_archive_corpus("b", out_dir=str(tmp_path), scrapes_dir=str(tmp_path / "no"))
    assert corpus == []
