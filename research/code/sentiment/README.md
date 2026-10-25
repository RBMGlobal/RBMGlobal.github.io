# Validated sentiment review adapter

This staged application performs no browsing, AI calls or publication. It takes collector-produced evidence, a provider-neutral candidate, a separate independent review attestation and a trusted policy. Python 3.12 and its standard library are sufficient. The public output is complete `data.json` and `review.json`; publication and live verification are separate runner responsibilities.

## Run and test

```
python sentiment/apply_review.py --baseline sentiment/data.json --candidate work/candidate.json --manifest work/sources/manifest.json --evidence-root work/sources --attestation work/attestation.json --policy policy.json --output work/validated-sentiment
python -m unittest discover -s sentiment/tests -v
node --test sentiment/tests/evidence-panel.test.cjs
```

Run the Python tests from this folder, or put this folder on `PYTHONPATH` when running them from its parent. The function signature is `apply_review(baseline_path, candidate_path, manifest_path, evidence_root, attestation_path, policy_path, output_dir, now=None)`. `now` is an optional timezone-aware datetime for deterministic testing. It returns the public review object. Invalid input raises `InvalidReview`; the CLI exits nonzero. Existing output directories are refused. Validation completes before any output is created; a temporary sibling directory is renamed only when both complete files exist.

`candidate.schema.json` is the complete structural schema, suitable for the research adapter's structured output. Runtime validation adds stricter dates, hashes, counts, geography, excerpt and numeric checks. Every object in candidate content rejects unexpected fields. `examples/synthetic-packet/` is a fully hash-bound test packet with invented, visibly labelled source text, not real research. Its dates are fixed; use `fixtures.NOW` when replaying it. Never publish synthetic examples or their output.

## Trust boundary and input contract

The runner, not candidate text, creates and stores the collector manifest, source extracts, policy and reviewer attestation. Hash binding detects changes after review; it is not a cryptographic identity signature. File separation alone cannot prove that two different model invocations took place. The orchestrator must perform the second, independently prompted review and bind its response to the exact candidate/manifest bytes only after all reviewer checks pass.

The manifest is schema version 1 with sources containing `id`, `url`, `final_url`, `title`, `publisher`, `published_at`, `fetched_at`, `http_status`, `body_sha256`, `text_sha256`, `extract_path`. Source text must be UTF-8, within the evidence directory, hash-identical to the receipt, nonempty and bounded. Only HTTP 200, ordinary HTTPS and policy-approved publisher domains/subdomains are accepted. The body digest records the collector's retrieval; this module can independently recompute the extract digest, not the body digest because raw body files are not part of this contract.

The combined trusted policy must contain `schema_version: 1`, `primary_hosts` and, if any poll is proposed, `polling_hosts`. `primary_hosts` may include original agency/NGO reports and original journalism for attributed security context. `polling_hosts` is narrower: the originating survey publishers (initially Afrobarometer, NOIPolls, Yiaga and Nigeria's NBS). A news article that reports a poll cannot supply its numeric findings. Matching is exact domain or dot-delimited subdomain, never a loose prefix. Other budget/discovery policy keys are ignored here. `state_rating_rules` defaults to an empty list.

The candidate requires `schema_version`, `baseline_sha256`, `evidence_manifest_sha256`, `review_date`, `polls`, `security_context`, `state_rating_proposals`. Citations are `{source_id, quote, locator}`. A quote must be an exact contiguous substring of the collected text. Publication date must match the collector's independently checked date. Exact fieldwork days must explicitly appear in the cited passage; a month-only source never becomes a guessed start/end range. Numeric values and relevant sample sizes must occur in the cited passages. Zonal/state sample and result passages must name the matching geography. Geographic canonical zone names are North Central, North East, North West, South East, South South and South West; state names include FCT.

The independently created attestation requires schema version 1, `approved: true`, candidate and evidence-manifest SHA-256, `reviewed_at`, `model`, `response_id`, and empty `issues`. It must postdate source collection, match the candidate review day and be at most 48 hours old. Optional `approved_state_changes` lists exact proposal IDs. These flags are never accepted from candidate content.

## Output and assessment rules

New national and zonal poll records append to `national_evidence` and `zonal_evidence`. The old national values and six zone scores remain unchanged. Valid state-specific evidence appends separately to `state_evidence`. Evidence publication, fieldwork and review dates stay distinct. Original sources and their incomplete historical provenance are retained without being falsely reverified. Duplicate source/publication/geography combinations are not appended again. Corrections or replacements to previously published evidence require a separately reviewed correction workflow; this adapter does not silently replace history.

Unchanged data is copied byte-for-byte, preserving every date. Accepted evidence may change `updated`, `updated_iso`, `evidence_updated` and the appropriate national-evidence date. It does not advance `assessment_updated`. Security context exists only in the public review record with attribution, publication date and reporting window; it never becomes approval polling. Review scope explicitly covers supplied sources, not exhaustive discovery. No X file, GDELT file or unrelated page is written.

State rating changes are withheld by default, with reasons in the review record. The exceptional path requires a directly matching state survey, explicit representative probability/random method, exact fieldwork after the existing assessment, a matching subject's direct approval measure, separate reviewer approval of the specific proposal and an owner-approved classification rule in trusted policy. A rule consists of subject, measure, approval_reference and four ordered `{minimum, tone}` bands covering 0–100 with unique cold/cool/mixed/warm tones. **No real thresholds are supplied or recommended.** The synthetic test rule is not an operating policy. A successful exceptional change records per-subject provenance and preserves the other states and subjects. Routine production prompts keep proposals empty.

Newly published poll prose and security context are capped at 200 derived words per publisher across the accepted packet, including sample/method/limitation text and applied state rationales. Exact private quotes are omitted from public JSON; independent review also checks paraphrase quality and source-use limits.

The deterministic checks prove schema consistency and evidence traceability, not semantic entailment or survey representativeness. The independent reviewer must check claims, question wording, relevant sample, geographic meaning, methodology, attribution, comparability, contradictions and copyright before attesting. A matched number alone is not sufficient research verification.

## UI integration

`integration/index.patch` adds three bounded integration changes to the current canonical page: the existing freshness paragraph gets an ID, `evidence-panel.js` is loaded, and the new renderer is called once validated dashboard data loads. The historical static panel remains a fallback if the new script cannot load. `integration/index.html` is a complete reviewable copy; canonical files are untouched by this staging work.

Copy `integration/evidence-panel.js` to `sentiment/evidence-panel.js`, apply the patch, and add that script plus `sentiment/review.json` to the company's explicit public build allowlist and live hash-verification manifest. Include the JS regression tests in CI. The optional review request cannot block state controls, maps or evidence display. Every dynamic field is escaped, external links accept only HTTPS without credentials, and historical Yiaga results/methodology remain visible alongside new sources. The component uses the existing details panel and scroll area. It does not alter map calculations, state data, X themes or layout.

Do not label a validated local packet as published. The hosted publisher must preserve the baseline, commit complete files, deploy, verify every artifact hash against the live site and check the evidence panel before reporting success.
