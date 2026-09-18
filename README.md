# paperclass

Standalone Paperless-ngx auto-classifier. Assigns `document_type`, `tags`,
`correspondent`, and `title` to newly uploaded documents by reading the actual
page image with a local vision model via Ollama (not just OCR'd text), so it
holds up on imperfect scans (e.g. Greek documents).

Fully independent: its own repo, container, and database - no dependency on
any other project. It just needs a Paperless-ngx URL/API token and an Ollama
URL/vision model to talk to.

## Quickstart

1. Clone this repository and navigate into it:
   ```bash
   git clone https://github.com/your-username/paperclass.git
   cd paperclass
   ```

2. Copy the example environment file and fill in your credentials:
   ```bash
   cp .env.example .env
   # Edit .env to set PAPERLESS_URL, PAPERLESS_TOKEN, and OLLAMA_URL
   ```

3. Build and run the container:
   ```bash
   docker build -t paperclass .
   docker run -d -p 8091:8000 --env-file .env -v paperclass-data:/data paperclass
   ```
   (Or, for local development: `python -m venv .venv`, activate it, `pip install -r requirements-dev.txt`, then `uvicorn app.main:app --reload`.)

4. Open the web GUI at `http://localhost:8091` to confirm the taxonomy loaded and review settings.

5. In Paperless, create (or edit) a Workflow with trigger **Document Added** and a **Webhook** action pointing at `http://<this-host>:8091/webhook/classify` (see "How it plugs in" below for full details).

## How it plugs in

1. Run the container somewhere that can reach both Paperless and Ollama.
2. In Paperless, add (or edit) a Workflow with trigger **Document Added**
   (all sources) and a **Webhook** action pointing at
   `http://<this-host>:<port>/webhook/classify`, with header
   `X-Paperclass-Secret: <your WEBHOOK_SECRET>` if you set one, and body
   `{"doc_url": "{{ doc_url }}"}`.
3. That's it - paperclass fetches its own copy of the document, classifies
   it, and PATCHes `document_type`/`tags`/`correspondent`/`title` back.

Candidate tags/types are read live from Paperless on startup (and refreshed
hourly), so it always classifies into whatever taxonomy Paperless currently
has - edit `CANDIDATE_TAGS` in `.env` if you want to change which tags the
model is allowed to choose from. Correspondents are not restricted to a
curated list - paperclass will fuzzy-match a suggested name against existing
correspondents, or create a new one if there's no confident match.

`title` is only ever overwritten when the document's current title still
looks auto-generated (empty, a bare filename, or a scanner default like
`Scan_2026-01-01`/`IMG_1234`) - a title a human wrote, or that a previous
confident run already set, is left alone.

`correspondent` is never allowed to be the document owner's own name -
edit `CORRESPONDENT_BLACKLIST` in `.env` (or the equivalent Settings-page
textarea, editable at runtime) to list the name/variants that should never
be suggested as a correspondent.

## Run it

### Local dev (fastest iteration)

```bash
python -m venv .venv
.venv/Scripts/activate   # or: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env     # fill in PAPERLESS_URL / PAPERLESS_TOKEN / OLLAMA_URL
uvicorn app.main:app --reload
```

### Dry-run accuracy check (no writeback)

Classify a batch of already-tagged real documents and compare against their
actual document_type/tags, without touching Paperless:

```bash
python -m scripts.dry_run 15
```

### Container

```bash
docker build -t paperclass .
docker run -d -p 8091:8000 --env-file .env -v paperclass-data:/data paperclass
```

## Web GUI

The service includes a lightweight, unauthenticated web interface (intended for
LAN-only access) at the root of the published port:

- **`/`** — Dashboard: shows recent classification runs (up to 50), with stats
  (total, successfully classified, needs review, errors, in progress).
- **`/test`** — Manual classification tester: paste a Paperless document ID to
  see what the model would classify it as (with image preview and confidence).
  Useful for tuning or debugging.
- **`/settings`** — Configure candidate tags (one per line), the correspondent
  blacklist (one per line), Ollama model name, document image DPI for
  processing, and taxonomy refresh frequency (in minutes) without restarting.

**Important**: This interface has no authentication by design (the whole service
assumes a trusted network). Do not expose it to the internet without adding
your own reverse-proxy authentication / HTTPS layer.

### Redeploying on a Podman/Quadlet host (e.g. a new homelab box)

1. `podman build -t localhost/paperclass:latest .`
2. Copy `deploy/paperclass.env.example` to
   `~/.config/paperclass/paperclass.env`, fill in the real values.
3. Copy `deploy/paperclass.container.example` to
   `~/.config/containers/systemd/paperclass.container`, adjust the network
   name/paths if different from the original host.
4. `systemctl --user daemon-reload && systemctl --user start paperclass.service`
5. Wire the Paperless Workflow's Webhook action at the container's published
   port, as above.

## Safety behavior

- Never invents a tag/type outside what Paperless already has.
- Low-confidence or failed classification -> tags with `Needs Review`
  instead of guessing; `document_type` is left untouched.
- Idempotent: re-running on the same document only removes tags paperclass
  itself previously applied, and never overwrites a `document_type` or
  `correspondent` a human has since corrected.
- Never writes the document owner's own name as a `correspondent` (see
  `CORRESPONDENT_BLACKLIST`), even if the model's answer ignores the prompt's
  instruction to avoid it.
- Audit trail in SQLite (`GET /api/runs`, or `sqlite3 data/paperclass.db`) -
  no UI, this is a headless automation service.
