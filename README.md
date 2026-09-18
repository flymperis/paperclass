# paperclass

A standalone, self-hosted classifier for [Paperless-ngx](https://docs.paperless-ngx.com/). When a
document is added, paperclass downloads its own copy, shows the actual scanned page **image** to a
local vision model via [Ollama](https://ollama.com/), and writes back `document_type`, `tags`,
`correspondent`, and `title`. Reading the image directly - not the OCR text Paperless already
extracted - is what makes it hold up on imperfect, skewed, or non-English scans that break
text-only classifiers.

It's fully independent: its own repo, container, and SQLite database. It only needs a
Paperless-ngx URL/API token and an Ollama URL/vision model to talk to.

## Quickstart

1. Clone the repository and copy the example environment file:

   ```bash
   git clone https://github.com/flymperis/paperclass.git
   cd paperclass
   cp .env.example .env
   ```

2. Edit `.env` and set `PAPERLESS_URL`, `PAPERLESS_TOKEN`, and `OLLAMA_URL` (see
   [docs/SETUP.md](docs/SETUP.md#configuration-reference) for every available setting).

3. Build and run the container:

   ```bash
   docker build -t paperclass .
   docker run -d -p 8091:8000 --env-file .env -v paperclass-data:/data paperclass
   ```

   (For local development instead: `python -m venv .venv`, activate it,
   `pip install -r requirements-dev.txt`, then `uvicorn app.main:app --reload`.)

4. Open `http://localhost:8091` and check the **Dashboard** loads and **Settings** shows the
   taxonomy you expect.

5. In Paperless, add a Workflow (trigger: **Document Added**) with a **Webhook** action pointing at
   `http://<this-host>:8091/webhook/classify`. Full field-by-field instructions - including two
   gotchas that will otherwise cost you an afternoon - are in
   [docs/SETUP.md](docs/SETUP.md#wiring-it-into-paperless-ngx).

That's it: new documents get classified automatically as they're added.

## Documentation

- **[docs/SETUP.md](docs/SETUP.md)** - full configuration reference, wiring paperclass into a
  Paperless-ngx Workflow, deployment (dev venv and Podman Quadlet), troubleshooting, and running
  the test suite / accuracy checker.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** - the web GUI, the classification pipeline, and
  the safety mechanisms (confidence gating, idempotent write-back, correspondent matching, generic
  title detection) that keep it from clobbering data you or Paperless already set correctly.

## Safety, in short

- Never invents a tag or document type outside what Paperless already has.
- Low confidence or a failed classification adds a `Needs Review` tag instead of guessing;
  `document_type` is left untouched in that case.
- Idempotent: re-running on the same document only ever removes tags paperclass itself previously
  applied, and never overwrites a `document_type`, `correspondent`, or `title` a human has since
  corrected.
- Never writes the document owner's own name as a `correspondent` (see the correspondent
  blacklist), even if the model's answer ignores the prompt's instruction to avoid it.
- Full audit trail in SQLite (`GET /api/runs`, or `sqlite3 data/paperclass.db`).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#confidence-gating-and-write-back-safety) for the
full explanation of how and why.

## Web GUI

A lightweight, unauthenticated web interface (LAN-only by design - see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#the-gui)) is served at the root of the published port:

- **`/`** - Dashboard of recent classification runs, with sortable columns and status counts.
- **`/test`** - Classify a document by id without writing anything back, then optionally apply the
  result for real.
- **`/settings`** - Live-editable taxonomy/tuning knobs (candidate tags, correspondent blacklist,
  model, DPI, refresh interval) - no restart required.

## License

[MIT](LICENSE)
