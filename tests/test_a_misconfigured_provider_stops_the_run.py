# -*- coding: utf-8 -*-
"""A provider that is not configured must stop the run, not quietly change the film.

MEASURED 2026-08-08. The Veo model id reached Vertex with literal quote characters inside it:

    400 INVALID_ARGUMENT — Invalid Endpoint name:
      projects/…/publishers/google/models/"veo-3.1-generate-001".

Every scene got the same refusal. The compositor's per-scene fallback fired FOUR TIMES and the
run continued toward a Ken Burns film — after five paid seed images had been generated to feed a
generator that was never reachable. The owner had asked for the opposite in as many words:
«انا عايزاه كله يبقا veo».

THE DISTINCTION THIS ADDS. A fallback answers "this shot failed". It cannot answer "this provider
is not configured", because that failure is identical for every scene and no later scene will come
right. So a permanent argument/permission/billing error stops on the FIRST scene with the vendor's
own words; 429s, 503s and timeouts keep the fallback they were written for.

The classifier is deliberately narrow — an unrecognised message stays transient, so a new vendor
string costs a fallback and not a refusal. That asymmetry is the safe one: a degraded reel is
visible in the log and in the audit, while a refused render is not a reel at all.
"""
from __future__ import annotations

from reel.compositor import _is_permanent_provider_error as permanent


def _err(kind: str, msg: str) -> Exception:
    return type(kind, (Exception,), {})(msg)


def test_the_exact_failure_that_degraded_a_whole_film_is_permanent():
    exc = _err("ClientError", "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
                              "'Invalid Endpoint name: projects/generate-504115/locations/"
                              "us-central1/publishers/google/models/\"veo-3.1-generate-001\".'}}")
    assert permanent(exc), "a malformed model id must stop the run, not degrade the film"


def test_a_credential_or_billing_problem_is_permanent():
    for msg in ("403 PERMISSION_DENIED",
                "404 NOT_FOUND: publisher model not found",
                "401 UNAUTHENTICATED",
                "API key not valid. Please pass a valid API key.",
                "billing account for this project is disabled"):
        assert permanent(_err("ClientError", msg)), f"{msg!r} will never come right on a retry"


def test_capacity_and_network_failures_keep_their_fallback():
    """These are the failures the per-scene fallback was written for: they really are per-scene."""
    for msg in ("429 RESOURCE_EXHAUSTED. Resource exhausted. Please try again later.",
                "503 UNAVAILABLE",
                "500 INTERNAL",
                "read timed out",
                "Connection aborted",
                "the generated clip had no video stream"):
        assert not permanent(_err("ClientError", msg)), (
            f"{msg!r} is transient — refusing the run would cost a reel that would have rendered")


def test_an_unrecognised_vendor_message_stays_transient():
    """The safe asymmetry: a new message the classifier has never seen degrades the film (visible
    in the log and the audit) rather than refusing to deliver one at all."""
    assert not permanent(_err("ClientError", "418 I_AM_A_TEAPOT: something new"))


def test_the_compositor_actually_consults_the_classifier():
    """The classifier existing is not the fix — the fallback path calling it is. Comments are
    stripped first: the comment explaining this fix names the function, and this repo has already
    shipped tests that matched their own prose."""
    import ast
    import inspect

    import reel.compositor as comp
    src = ast.unparse(ast.parse(inspect.getsource(comp.render_reel)))
    at = src.find("_is_permanent_provider_error")
    assert at > 0, "the fallback no longer distinguishes a misconfiguration from a failed shot"
    assert "raise RuntimeError" in src[at:at + 400], (
        "the permanent error is detected and then not acted on — detected is not handled")
