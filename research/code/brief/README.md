# Staged weekly brief packager

This is a local, provider-neutral renderer and validator. It makes no API calls,
does not research, commit or publish, and never edits the supplied accepted site.
It is installed as a validated publication component; paid activation is separately gated.

The source collector and separate reviewer produce a candidate packet, evidence
manifest and approved review attestation bound to both SHA-256 hashes. The
packager checks the structural and byte-level contract, not the truth or
completeness of journalism. Publication must retain independent source checks,
the separate content review, verified approved-artifact retrieval, deployment
and live-hash gates. Text is labelled **AI-generated, source-checked**.

## Run

Use Python 3.12 on Linux or a compatible local Python, install `requirements.txt`,
then run:

```
python build_brief.py --packet research/packet.json \
  --attestation research/attestation.json --manifest research/manifest.json \
  --evidence research/sources \
  --existing-site accepted-brief --events-release accepted-events/release.json \
  --events-image accepted-events/lastweek.png --output new-brief-package
```

The output directory must not exist. The command first validates all inputs,
builds in a separate temporary directory, validates complete outputs and archive
preservation, then renames that directory into place. Failures leave the accepted
site unchanged and no partial target. No credentials are needed.

`site/` is the complete static publication, including old PDFs, images and HTML
archives. `changes.json` lists complete changed/new files with byte lengths and
hashes for an atomic repository commit. `publication.json` hashes the complete
site and records the packet, attestation and source manifest hashes. `evidence/`
contains only the candidate's short evidence snippets, source metadata/hashes,
review receipt and accepted events release, outside public `site/`. Full source
extracts are never copied to the package. Do not upload the package root as a
Pages artifact. For a separately authenticated public approved packet, omit
`--evidence` and pass `--verified-public-input`. The caller must first verify the
approved artifact's expected origin and hashes; this switch is not an approval.

## Packet contract, version 1

See `tests/fixtures.py` for a complete deliberately synthetic example. Required:

- `schema_version: 1`, `issue_date`, `previous_issue_date`, `publication_date`,
  `reviewed_at` (timezone-aware timestamp), `reporting_window: {from, to}`.
- `overview`, `humanitarian_context`, `limitations`: arrays of paragraph objects.
- `developments`: array of `{title, event_window: {from, to} | null,
  event_timing_note, paragraphs}`. A source publication date is never substituted
  for an unknown event date. Outside-window developments are labelled by the
  renderer; an unknown event date needs an explicit note.
- `zones`: exactly the six Nigerian zones, each `{zone, paragraphs}`. Empty
  selections use `{kind: "coverage_gap", text: "...", source_ids: [],
  evidence: []}`; the renderer appends the explicit no-absence-inference caveat.
- Paragraphs: `{kind: "reported" | "analysis" | "limitation" | "coverage_gap",
  text, source_ids: [...], evidence: [{source_id, quote}]}`. Every factual/analysis
  paragraph must name sources and contain exact supporting source excerpts.
  Attributed reports and analysis are labelled distinctly. There is no separate
  model-supplied numerical totals field. Every numeric token in prose must occur
  in a cited evidence quote; this is a typo/invention guard, not entailment proof.
  Quantitative entailment, written-out numbers and source/date/authorship claims
  must also pass the upstream verifier.

The separate collector-owned manifest has `schema_version: 1` and `sources`, a
nonempty array of `{id, url, final_url, title, publisher, published_at, fetched_at,
http_status, body_sha256, text_sha256, extract_path}`. Publication dates are ISO
dates; fetched timestamps include timezones; HTTP status must be 200. UTF-8 source
files are named by relative `extract_path` and must match `text_sha256`. Sources
must be public HTTPS pages. A shared manifest can contain additional sources;
only cited sources are rendered in the brief. Unknown fields are rejected to prevent accidentally packaging full
articles, private paths or unrelated provider response content.

The separate reviewer attestation contains `schema_version: 1`, `approved: true`,
`candidate_sha256`, `evidence_manifest_sha256`, `reviewed_at`, `model`,
`response_id`, `issues: []`. This matches the central review contract. A negative
review, unresolved issue or changed candidate/manifest blocks the build.
Reviewer instructions must test source authenticity, dates, claim support,
numbers, contradictions and safe scope; the renderer cannot infer those from a
receipt. This local contract does not provide cryptographic identity. The caller
must isolate the reviewer from the research generator and authenticate the
published approved receipt. Schema-valid model text alone is insufficient.

Each publisher is limited to 200 derived words across the candidate's supported
prose, titles and timing notes, and 25 words of unique exact supporting excerpts
and source article titles.
Repeated identical excerpts are counted once; distinct overlapping excerpts are
counted conservatively. Full fetched articles remain private producer inputs.

## Date and evidence rules

An issue covers the next complete Monday-Sunday window after the existing issue;
the issue date is the following Monday. Publication can be later for catch-up,
but cannot precede the issue or be in the future. A retry of an already existing
issue is rejected; redeploy the accepted package instead. Existing files are
preserved byte-for-byte except `index.html`, `latest.pdf`, and the generated
`brief-release.json`; every new issue gets dated HTML and PDF, and the former
latest HTML is archived if it did not already have its own dated archive.

The events image must match the release's SHA-256 and byte count and decode as a
complete PNG. The displayed UCDP/GDELT image-panel windows are the seven days
ending at each source's maximum event date, matching the current map renderer.
The brief's reporting window, ingestion-through date and each source's maximum
event date are printed separately. No model-supplied map count or window is used.
Older UCDP data is labelled as lagged. GDELT is reporting activity, never a verified
incident/casualty count or opinion poll. The map can extend outside the reporting
week; that difference is explicit and the captured image filename is its release
date, not a falsely implied reporting-window date.

## Tests and limits

```
python -m unittest discover -s tests -v
```

Tests use invented, prominently labelled fixture evidence only and never make
network requests. The demonstration output is for layout/regression review and
must never be published as a real brief. ReportLab supplies Linux-portable PDF
generation and deterministic metadata. All user/source text is escaped in HTML
and ReportLab markup. PDF text, pages, source link annotations, every glyph and
image's safe page bounds, complete file hashes, archive immutability and local
asset targets are validated before success. The production gate uses these
automated checks; development preview pages are also rendered and inspected.

Source collection, licensing, factual verification, editorial topic selection,
model costs, workflow scheduling, credentials, repository mutation and live
deployment are deliberately outside this module. On missing/contradictory or
insufficient evidence the upstream system must decline publication, retain the
last accepted issue and record a clear failure or no-material-change result.
