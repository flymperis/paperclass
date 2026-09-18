# Setup guide

This is the detailed walkthrough: every configuration option, how to wire paperclass into a real
Paperless-ngx Workflow, both deployment paths, and how to troubleshoot it when something doesn't
work. For the fast path, see the [README quickstart](../README.md#quickstart).

## Configuration reference

paperclass has two layers of configuration:

- **Connection settings** - read once from the environment (`.env` or your container's env file)
  at process startup. Changing these requires a restart.
- **Runtime settings** - a handful of day-to-day tuning knobs, seeded once from the environment on
  first run, then stored in the SQLite database and editable live from the **`/settings`** page
  with no restart. Once paperclass has started once, editing these values in `.env` has no further
  effect - use the Settings page instead.

### Connection settings (`.env`, restart required)

| Variable | Default | Meaning |
|---|---|---|
| `PAPERLESS_URL` | `http://192.168.1.100:8000` | Base URL of your Paperless-ngx instance. **Not the same setting as Paperless's own `PAPERLESS_URL`** - see the [Workflow gotchas](#wiring-it-into-paperless-ngx) below, the two are easy to confuse because they share a name. |
| `PAPERLESS_TOKEN` | *(empty)* | Paperless API token (Paperless UI: My Profile → API Token). Required for any real use. |
| `OLLAMA_URL` | `http://192.168.1.100:11434` | Base URL of your Ollama server. |
| `OLLAMA_MODEL` | `qwen3.5:4b` | Vision-capable model tag to use. Must already be pulled in Ollama (`ollama pull <model>`). Only used to *seed* the runtime setting on first run - change the active model later via `/settings`. |
| `OLLAMA_NUM_CTX` | `8192` | Context window size passed to Ollama for each classification call. |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | HTTP timeout per Ollama call. One automatic retry on timeout before the run is marked an error. |
| `OLLAMA_KEEP_ALIVE` | `30s` | How long Ollama keeps the model loaded in memory after a call (Ollama's own `keep_alive` semantics - a plain number is treated as seconds, `-1` means forever). |
| `CANDIDATE_TAGS` | a starter list, see `app/config.py` | JSON array of tag names the model is allowed to choose from, e.g. `CANDIDATE_TAGS=["Bill","Receipt","Medical"]`. Only seeds the runtime setting on first run - edit the live list via `/settings`. |
| `CORRESPONDENT_BLACKLIST` | the document owner's own name/variants, see `app/config.py` | JSON array of names that must never be written as a `correspondent` (see [ARCHITECTURE.md](ARCHITECTURE.md#correspondent-matching-and-the-blacklist)). Only seeds the runtime setting on first run. |
| `NEEDS_REVIEW_TAG` | `Needs Review` | Tag applied when classification is low-confidence or fails. Auto-created in Paperless if it doesn't exist yet. |
| `TAXONOMY_REFRESH_MINUTES` | `60` | How often (in the background) to reload tags/document types/correspondents from Paperless. Only seeds the runtime setting on first run. |
| `CLASSIFY_DPI` | `150` | DPI used when rendering the page image sent to the model. Only seeds the runtime setting on first run. |
| `WEBHOOK_SECRET` | *(empty)* | Shared secret required in the `X-Paperclass-Secret` header on `POST /webhook/classify` and `GET /api/runs`. Empty disables this check entirely - fine for a trusted LAN, but set it if the port is reachable from anywhere less trusted. |
| `LOG_RAW_WEBHOOKS` | `false` | Log the raw headers/body of every inbound webhook call. Turn this on temporarily while wiring up a new Paperless Workflow so you can see exactly what Paperless sent. |
| `DATA_DIR` | `./data` | Where the SQLite database (`paperclass.db`) lives. |

Values with a `list[str]` type (`CANDIDATE_TAGS`, `CORRESPONDENT_BLACKLIST`) must be valid JSON
arrays in the env file, not comma-separated text.

### Runtime settings (`/settings` page, no restart needed)

| Setting | What it does | Sensible default |
|---|---|---|
| Ollama model | Model tag used for every classification call | Whatever a vision-capable model you've pulled into Ollama is called |
| Classify DPI | Resolution used to render the page image before sending it to the model | `150` - higher is sharper but slower |
| Taxonomy refresh (minutes) | How often candidate tags/document types/correspondents are reloaded from Paperless | `60` |
| Candidate tags | One tag name per line - the only tags the model may ever choose | Match this to the tags you actually use in Paperless |
| Correspondent blacklist | One name per line - names that must never be written as a `correspondent` | Your own name, and any spelling/accent variants that might appear on scanned mail addressed to you |

Document types and correspondents themselves aren't restricted to a curated list: document types
are offered to the model in full (there are usually few of them, and you manage that list
directly in Paperless), and correspondents are fuzzy-matched against everything Paperless already
has, or created new if there's no confident match.

## Wiring it into Paperless-ngx

1. In Paperless-ngx, go to **Workflows** and create (or edit) one with:
   - **Trigger**: `Document Added` (all sources).
   - **Action**: `Webhook`, with:
     - **URL**: `http://<paperclass-host>:<port>/webhook/classify` (e.g.
       `http://192.168.1.50:8091/webhook/classify`).
     - **As JSON**: enabled (`true`).
     - **Body**: `{"doc_url": "{{ doc_url }}"}`
     - **Headers**: `X-Paperclass-Secret: <your WEBHOOK_SECRET>` - only needed if you set one.
2. Save and enable the Workflow. New documents will now be queued for classification the moment
   Paperless finishes processing them.

paperclass fetches its own copy of the document from Paperless (it never trusts the webhook body
for anything beyond the document id), classifies it, and `PATCH`es `document_type` / `tags` /
`correspondent` / `title` back.

### Two gotchas you will otherwise hit

These were both discovered the hard way while building paperclass. Its webhook parser already
defends against both, but if you're configuring your *own* Paperless Workflow by hand, you need to
know about the first one - paperclass can't fix a problem that happens entirely on Paperless's
side.

**1. Paperless's own `PAPERLESS_URL` must be set on the Paperless server, or `{{ doc_url }}` is
empty.** Paperless-ngx builds the `{{ doc_url }}` template variable used in Workflow webhook
bodies from its *own* `PAPERLESS_URL` environment variable (the base URL Paperless itself is
configured to think it's reachable at). If that variable is unset on your Paperless
installation, `{{ doc_url }}` renders as `null`/empty in the webhook body, and there is no
document id for paperclass (or anything else) to extract. This is Paperless's own setting, on
Paperless's own container/host - it is a different thing from paperclass's `PAPERLESS_URL`
setting in this project's `.env`, even though the two happen to share a name. If webhooks fire
but paperclass logs "could not extract a document id from body", this is almost always the cause:
set `PAPERLESS_URL` in your Paperless-ngx environment to Paperless's own externally-reachable base
URL and restart Paperless.

**2. Paperless's `as_json: true` webhook option double-encodes the body.** With `as_json` enabled,
Paperless sends the request body as a JSON string *literal* containing the templated JSON text -
i.e. the HTTP body is something like `"{\"doc_url\": \"http://.../documents/123/\"}"`, a quoted
string, not a plain JSON object `{"doc_url": "..."}`. paperclass's webhook handler
(`app/routers/webhook.py`) already accounts for this: it `json.loads()`s the body, and if the
result is itself a string (rather than a dict), parses it a second time. You don't need to do
anything about this yourself - it's mentioned here so that if you ever inspect the raw webhook
payload (e.g. with `LOG_RAW_WEBHOOKS=true`) and it looks "double-quoted" and confusing, you know
that's expected and not a misconfiguration.

## Deployment

### Local dev (fastest iteration)

```bash
python -m venv .venv
.venv/Scripts/activate   # or: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env     # fill in PAPERLESS_URL / PAPERLESS_TOKEN / OLLAMA_URL
uvicorn app.main:app --reload
```

### Container (Docker or Podman, manual run)

```bash
docker build -t paperclass .
docker run -d -p 8091:8000 --env-file .env -v paperclass-data:/data paperclass
```

(Substitute `podman` for `docker` throughout if that's your runtime - the `Dockerfile` has no
Docker-specific features.)

### Podman Quadlet (production, e.g. a homelab host)

This is the deployment shape the templates under `deploy/` are written for -
[`deploy/paperclass.container.example`](../deploy/paperclass.container.example) and
[`deploy/paperclass.env.example`](../deploy/paperclass.env.example).

1. Build the image on the target host: `podman build -t localhost/paperclass:latest .`
2. Copy `deploy/paperclass.env.example` to `~/.config/paperclass/paperclass.env` and fill in the
   real `PAPERLESS_URL`, `PAPERLESS_TOKEN`, `OLLAMA_URL`, `OLLAMA_MODEL`, and `WEBHOOK_SECRET`
   values.
3. Copy `deploy/paperclass.container.example` to
   `~/.config/containers/systemd/paperclass.container`. The example assumes:
   - A pre-existing Podman network called `homelab.network` (change `Network=` if yours is named
     differently, or remove the line to use the default network).
   - Data persisted at `%h/homelab/paperclass/data` on the host, mounted to `/data` in the
     container (`Volume=%h/homelab/paperclass/data:/data:Z` - adjust the host path if you keep
     data elsewhere; the `:Z` SELinux label is required on SELinux-enforcing hosts and harmless
     otherwise).
   - The service published on host port `8091` (`PublishPort=8091:8000`).
4. `systemctl --user daemon-reload && systemctl --user start paperclass.service`
5. Confirm it's healthy: `systemctl --user status paperclass.service`, then
   `curl http://localhost:8091/api/health`.
6. Wire the Paperless Workflow's Webhook action at `http://<this-host>:8091/webhook/classify`, as
   described [above](#wiring-it-into-paperless-ngx).

## Troubleshooting

**Container/service won't stay healthy, or is immediately marked unhealthy.** Check the
`HealthCmd` line in `paperclass.container` (or the `HEALTHCHECK` line in the `Dockerfile`) hasn't
had its quoting altered. The working form is a single `python -c "..."` command where the *outer*
quotes are double quotes and the URL *inside* the Python code is single-quoted:

```
HealthCmd=python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)"
```

Mismatched or reordered quoting here (e.g. swapping which quote character is outermost) can cause
the systemd/Podman unit parser to split the command incorrectly, so the health check runs a
truncated, broken command and the service is reported unhealthy even though the app itself is
running fine. If you edit this line, keep that quote nesting.

**Classifications never happen for new documents.** Work through these in order:

1. Is the Paperless Workflow actually enabled, with trigger `Document Added` and action
   `Webhook`?
2. Does the Webhook URL include the full path, `/webhook/classify`, and the correct port?
3. Does the webhook body match `{"doc_url": "{{ doc_url }}"}` (or include a `paperless_id` field
   directly)?
4. Is Paperless's own `PAPERLESS_URL` environment variable set on the Paperless server? (See
   [gotcha 1](#two-gotchas-you-will-otherwise-hit) above - this is the single most common cause.)
5. Set `LOG_RAW_WEBHOOKS=true` in paperclass's `.env`, restart it, trigger a test document, and
   check the logs for the raw payload Paperless actually sent, plus any "could not extract a
   document id" warning.
6. Check `GET /api/health` returns `{"status": "ok"}` and that Paperless can reach paperclass's
   host/port at all (network/firewall).

**A run shows `classified` on the Dashboard, but `tags`/`document_type`/`correspondent` didn't
actually change on the document in Paperless.** Check the run's `reason` field (via `/api/runs` or
`sqlite3 data/paperclass.db`) for a note like `document_type skipped: manually corrected` or
`correspondent skipped: manually corrected`. This is the idempotent write-back protection working
as intended, not a bug: if the document's current value differs from what paperclass itself last
set, paperclass assumes a human corrected it and deliberately leaves it alone rather than
overwriting a real correction. See
[ARCHITECTURE.md](ARCHITECTURE.md#confidence-gating-and-write-back-safety) for the full rule. If
you genuinely want paperclass to reclassify a document from scratch, editing the value in
Paperless back to whatever paperclass previously set (or clearing it) will let the next run apply
its new answer again.

**Ollama errors** (`model ... is not installed`, timeouts, connection refused). Confirm the model
in `/settings` (or `OLLAMA_MODEL`) has been pulled on the Ollama server (`ollama pull <model>`),
that `OLLAMA_URL` is reachable from wherever paperclass runs, and consider raising
`OLLAMA_TIMEOUT_SECONDS` if you're running a large model on modest hardware.

**Everything ends up `Needs Review`.** Use `/test` against a specific document id to see the raw
model JSON output and the rendered page image paperclass actually sent - this usually reveals
whether the DPI is too low to read the text, the model isn't confident enough in its own answers,
or the taxonomy (candidate tags/document types) doesn't match what's really in your archive.

## Development

Run the test suite:

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests never touch your real `.env`, Paperless instance, or `data/paperclass.db` - `tests/conftest.py`
forces a throwaway SQLite database and fake connection settings before any application module is
imported.

### Checking classification accuracy before trusting a change

Before changing the prompt (`prompts/classify_prompt.tmpl`) or the taxonomy, it's worth checking
whether the change actually improves things against documents you've already tagged for real, and
`scripts/dry_run.py` does exactly that: it classifies a batch of already-classified documents from
your real Paperless instance and compares the model's answer against the ground truth, without
writing anything back.

```bash
python -m scripts.dry_run 15   # classify the 15 most recent already-tagged documents
```

It prints a per-document comparison plus an overall score for document type, tag recall, and
correspondent matching - a quick sanity check that a prompt/model/DPI change didn't quietly make
things worse.
