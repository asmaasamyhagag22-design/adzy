# Public offline ADZY demo

Python3.12, Docker, Node22 for checks and Playwright Chromium are required. Use the application lock from the project root:

```bash
python3 -m venv .venv-app
source .venv-app/bin/activate
python -m pip install -r requirements-app.txt
python -m pip check
python -m playwright install --with-deps chromium
docker pull postgres:16-alpine
python -m workflows.app_demo init --directory .review-app-demo
python -m workflows.app_demo serve --directory .review-app-demo --port 8000 --synthetic-posters accepted
```

Open [ADZY](http://127.0.0.1:8000/app). Use the email printed by init and `SyntheticDemo42`. Think → Review strategy → Lock this strategy → Prepare the poster → Make the ad. Evidence, provider responses and decisions in this fictional demo are synthetic; the UI, authentication, PostgreSQL, LangGraph/LangChain tools, persistence and exact download services are real application code. Setup never approves a real strategy or authorizes a paid call. The demo blocks external provider network calls.

Stop and restart serve with `--synthetic-posters draft` to inspect an unresolved draft, or `--synthetic-posters rejected` to inspect saved rejection/reasons. Rejected candidates offer **Review saved checks** and no unavailable download; a previous accepted revision remains accessible only when one exists. Mobile navigation stays in document flow.

For an optional local Langfuse SDK capture, add `--capture-sdk`. This writes redacted synthetic in-memory observations on shutdown; hosted server ingestion remains unverified. Semantic assessment remains shadow/unqualified and cannot grant acceptance.

After stopping this disposable demo:

```bash
python -m workflows.demo cleanup --directory .review-app-demo
```

This public demo requires no Firefox artwork, private dataset, authorization packet, provider credential or account. The old portfolio helpers are historical optional case tools, not public demo commands; their private case/artwork bundle is deliberately absent. Tests that exercise authorization helpers construct their own fictional inputs.

Three distinct histories remain explicit: the complete synthetic app journey; two real Firefox renders that were visually rejected at12/12 with no accepted final download; and a manually directed offline improved preview without canonical acceptance. Historical real/local images are retained only in the original workspace and not redistributed here.

[Final interface screenshots](../images/poster-v1-final-rejected-desktop.png) · [mobile](../images/poster-v1-final-rejected-mobile.png) · [asset disposition](../../THIRD_PARTY_ASSETS.md).


### Evidence-aware copy editing

With the synthetic app running, approve its strategy, create a poster and select **Edit copy** in Create. Preview unsupported wording, choose **Use source wording**, save an independent revision, compare with Original, then reload and export the draft. The local editor never calls a provider. Historical posters without a managed text-free background and structured inputs show an explicit unavailable reason.

Run the complete browser journey, including response loss, a late preview, restart and checksum downloads:

```bash
python tools/verify_copy_editor_browser.py --directory /tmp/copy-editor-demo --evidence-dir /tmp/copy-editor-browser
```

The browser tool explicitly materializes a fake QA fixture in its guarded disposable database to exercise final delivery. This fixture operation is absent from the application API and does not qualify a real image. See [copy editor details](../../COPY_EDITOR_RESULT.md).
