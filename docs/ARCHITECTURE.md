# Architecture

How paperclass is put together internally, what each web page does, and - most importantly - the
rules that keep an automated classifier from quietly corrupting your document archive.

## Pipeline overview

```
Paperless Workflow (Document Added)
        │  POST /webhook/classify  {"doc_url": "{{ doc_url }}"}
        ▼
app/routers/webhook.py  ── extracts the Paperless document id
        │
        ▼
app/worker.enqueue()  ── the single place that turns an id into a durable, deduplicated
        │                 QUEUED row in the `classification_runs` table (SQLite)
        ▼
app/worker.worker_loop()  ── single-consumer FIFO: classifies one document at a time,
        │                     because the GPU can only hold one Ollama model resident at once
        ▼
app/classifier.classify()  ── renders page image(s), calls Ollama with a JSON-schema-
        │                      constrained prompt, validates/gates the answer on confidence
        ▼
app/writeback.apply()  ── idempotently PATCHes document_type/tags/correspondent/title
                            back to Paperless, never clobbering a human correction
```

`worker.enqueue()` is shared by two callers: the Paperless webhook (new documents) and the
`/test/apply` button in the GUI (manual re-classification of an existing document). Both go
through the exact same dedupe/queue logic, so a double-fired webhook and a double-click on
"Apply" are handled identically - see `enqueue()`'s docstring in `app/worker.py` for the exact
`queued` / `already_queued` / `recently_processed` semantics.

The queue itself is DB-backed rather than in-memory: the `classification_runs` table doubles as
both the audit log and the queue, so a restart never silently drops a document that was waiting to
be processed. Any row still `PROCESSING` at startup is reset back to `QUEUED` (`reset_stale()` in
`app/worker.py`) - safe because classification and write-back are both idempotent.

## The GUI

Served directly by FastAPI (`app/routers/ui.py`, Jinja2 templates in `app/templates/`) at the root
of the published port. It is intentionally unauthenticated - the whole service assumes a trusted
LAN. Do not expose it to the internet without your own reverse-proxy authentication/HTTPS layer.

- **`/` - Dashboard.** The last 50 classification runs, newest first, with summary counts (total,
  classified, needs review, error, in progress). Columns are sortable client-side by clicking a
  header (document id, duration are numeric sorts; everything else is a text sort) - this is pure
  front-end JavaScript over the rows already rendered, no extra request. Each document id links to
  `/test` pre-filled with that id.
- **`/test` - Manual classification tester.** Enter a Paperless document id and paperclass fetches
  it, renders a preview of page one, and runs the classifier - showing the chosen document type,
  tags, correspondent, title, confidence, and the raw model JSON. This is entirely read-only:
  nothing is written back yet. If a result was produced, an **"Apply this classification for
  real"** button appears in a clearly marked warning card. Clicking it does not apply the
  in-memory result you just saw directly - it calls `worker.enqueue()`, putting the document on
  the same background FIFO queue the Paperless webhook uses, and a fresh classification runs
  (and is written back) asynchronously when the queue reaches it. The page says as much, and
  points you at the Dashboard to see the outcome, precisely because the model only runs one
  document at a time and this action must never race the background worker with a second
  concurrent Ollama call.
- **`/settings` - Live configuration.** Edit candidate tags, the correspondent blacklist, the
  Ollama model, classification DPI, and the taxonomy refresh interval, all applied immediately
  (see [SETUP.md](SETUP.md#runtime-settings-settings-page-no-restart-needed)) - no restart. The
  Paperless/Ollama connection URLs are shown for reference but are read-only here; they're
  controlled by `.env` and require a restart to change.

## Confidence gating and write-back safety

### Independent confidence per field

The model's JSON answer (constrained to a schema built from your live Paperless taxonomy - see
`Taxonomy.schema()` in `app/taxonomy.py`) includes a `_confidence` flag (`"high"`/`"low"`) for each
of `document_type`, `tags`, `correspondent`, and `title`, and each is gated independently
(`app/classifier.py`):

- **`document_type`** is applied whenever the model reports high confidence in it - regardless of
  how confident it is about tags, correspondent, or title.
- **`tags`** are only applied when the model is *also* independently confident in them, since
  they're the noisier half of the answer (e.g. "Shopping" vs "Electronics" vs "Home" ambiguity). A
  confident document type with low-confidence tags results in "type set, no tags added" rather
  than "needs review" - forcing a tag guess is worse than adding none.
- **`correspondent`** and **`title`** are each dropped (treated as empty) unless the model is
  confident in them specifically, independent of everything else.
- If `document_type` itself isn't confident (or doesn't match a real Paperless type), the whole
  run is marked `needs_review`: the `Needs Review` tag is added and `document_type` is left
  completely untouched, rather than guessing.

### Idempotent write-back rules

Paperless workflows (and Celery retries) can fire more than once for the same event, and a human
may reclassify a document paperclass already touched. `app/writeback.apply()` is written so that
re-running on the same document is always safe:

- **Tags are additive-only.** A re-run only ever *removes* tags that paperclass itself applied on
  a previous run (tracked in `applied_tag_ids`) - any tag a human added independently is left
  alone forever. The new tag set is `(live tags − previously-applied-by-us) ∪ newly-decided`.
- **`document_type` and `correspondent` are only touched when unset, or still equal to what
  paperclass itself last set.** If the live value differs from `applied_document_type_id` /
  `applied_correspondent_id` recorded on the previous run for that document, that means a human
  changed it since - paperclass leaves it alone and records a note like `document_type skipped:
  manually corrected` in the run's `reason` field instead of overwriting it. This is the same
  intent as paperless-gpt's `PRESERVE_EXISTING_METADATA`.
- **A blacklisted correspondent (the document owner's own name/variants) is dropped defensively at
  write-back time too**, even if it somehow made it past the prompt and the classifier's own
  check - see [Correspondent matching and the blacklist](#correspondent-matching-and-the-blacklist)
  below.
- **`title`** has no separate "applied" tracking - instead, it's only overwritten when the
  document's *current* title still looks auto-generated (see below). A human-written title, or a
  title a previous confident run already set, no longer looks generic, so later runs naturally
  leave it alone.
- The previous run's applied values come from the most recent *completed* run for that document id
  (`CLASSIFIED` or `NEEDS_REVIEW`, never `ERROR` or `PROCESSING`) - an errored run never counts as
  a baseline to compare against.

### Generic-title detection

`looks_generic_title()` in `app/writeback.py` decides whether the document's current title is
"safe to overwrite": empty/whitespace, a scanner default (`Scan_2026-01-01`, `IMG_1234`), a bare
filename-like word (`document`, `file`, `attachment`, `downloaded`, `new document`, optionally
with a trailing `(2)`/`_2`), a bare timestamp, or a bare UUID all count as generic. Anything else -
including a title paperclass itself confidently wrote on a previous run - is assumed to be
meaningful and is never touched. See `tests/test_writeback.py` for the exact set of patterns this
does and doesn't match.

### Correspondent matching and the blacklist

Unlike tags and document types, correspondents are **not** restricted to a curated list - any name
the model suggests can become a new Paperless correspondent, or be matched onto an existing one.
`Taxonomy.match_correspondent()` (`app/taxonomy.py`) normalizes names (strips punctuation, folds
case) and does a fuzzy match against every correspondent Paperless already has:

- An exact normalized match reuses that correspondent's id.
- A very close match (`difflib.SequenceMatcher` ratio ≥ `0.86`, or one name fully containing the
  other once both are long enough) also reuses the existing id - e.g. "ΔΕΗ" and "ΔΕΗ Α.Ε." resolve
  to the same correspondent instead of creating a near-duplicate.
- Anything below that threshold gets a brand-new correspondent created in Paperless. The threshold
  is intentionally high: merging two genuinely different senders into one correspondent by mistake
  is considered worse than occasionally creating a near-duplicate.

Separately, `is_blacklisted_correspondent()` compares a suggested name (again normalized) against
the correspondent blacklist and refuses to use it if it matches - this is how paperclass avoids
ever filing the document owner's own name (which legitimately appears on plenty of incoming mail,
e.g. as the addressee of a bill) as if it were the sender. This check happens twice: once in the
classifier right after the model answers (so a blacklisted name is dropped before confidence
gating even applies), and again defensively in `writeback._resolve_correspondent_id()` before
anything is actually written.

## Taxonomy refresh

`app/taxonomy.py`'s `Taxonomy` class holds the live set of document type ids, candidate tag ids,
and correspondent ids, fetched from Paperless. It's loaded once at startup and refreshed on a
background loop (`refresh_loop()`) at the interval configured by "Taxonomy refresh (minutes)" in
`/settings`, and also refreshed immediately whenever settings are saved. This means adding a new
document type or tag in Paperless (and, for tags, also adding it to the candidate tags list in
`/settings`) becomes available to the classifier without restarting paperclass - within one
refresh interval, or immediately after a settings save.

Candidate tags are an explicit allowlist (not "every tag Paperless has") so the model is never
offered unrelated bookkeeping tags from other tools. Document types are offered in full since
there are usually few of them and you manage that list directly. Correspondents are also offered
in full, since (unlike tags/types) any existing correspondent is fair game to reuse.

## Data model

Everything lives in one SQLite file (`data/paperclass.db`, path controlled by `DATA_DIR`):

- **`classification_runs`** - one row per classification attempt. Doubles as both the durable FIFO
  queue (`status` starts at `QUEUED`, becomes `PROCESSING`, then a terminal
  `CLASSIFIED`/`NEEDS_REVIEW`/`ERROR`) and the permanent audit trail (model's raw answer, what was
  actually applied, confidence, duration, any skip reason). Inspect it via `GET /api/runs` or
  directly with `sqlite3 data/paperclass.db`.
- **`runtime_config`** - a single row (id=1) holding the live-editable settings described in
  [SETUP.md](SETUP.md#runtime-settings-settings-page-no-restart-needed). Seeded from `.env` the
  first time paperclass starts against an empty database; edited thereafter via `/settings`.

`app/db.py` adds any newly-introduced columns to existing tables automatically at startup
(`_add_missing_columns()`) - a lightweight substitute for a full migration tool, appropriate for a
single-table SQLite database that only ever grows columns, not one this project intends to scale
into needing Alembic.
