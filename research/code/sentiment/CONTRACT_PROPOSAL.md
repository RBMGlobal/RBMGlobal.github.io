# Proposed sentiment candidate contract (awaiting integration agreement)

The collector's evidence files and manifest are separate from the AI candidate. The AI never supplies a trustworthy `verified: true` assertion. A source reference is accepted only when it resolves to a collector receipt, an allowed primary publisher URL, HTTP success and a local extract whose bytes match the receipt's digest. Every factual addition points to an exact excerpt and locator in that extract. These checks establish source retrieval and traceability; they are not a mathematical proof that an AI interpreted the passage correctly.

The candidate is bound to the exact baseline data bytes and evidence-manifest bytes by SHA-256. Unknown fields/operations are rejected. An invalid candidate produces no partial output. A valid candidate produces complete `data.json` and a minimal public `review.json` in a new output directory, with input hashes and source dates. Raw fetched text, provider responses, prompts and credentials are not public output.

Initial automatic operations proposed:

- Append separately dated national survey evidence with its own measurement names, publication date, fieldwork precision/window, sample, population, geography, methodology, weighting, limitations and comparability. A national measure is never copied into a zone/state field, and unlike surveys are not silently turned into a trend.
- Add explicitly attributed public-security context to the review record; it does not become opinion polling or change approval labels.
- Record no material change within the collector's stated search scope. A failed or incomplete collection cannot be represented as a complete no-change review.

Existing national baselines, six zonal records, 37 state/FCT records, historical X material, original source list and classification are preserved. Proposed state/zonal rating changes are rejected from automatic application pending a separately agreed rule for locally measured evidence. Assessment dates never advance merely because a scheduled review ran or new national evidence was appended. A valid no-change review preserves the exact original data bytes, including all date fields.

All new survey percentages require a named measure, finite 0–100 value and passage containing that value. Unknown fieldwork days stay null; publication dates are separate from fieldwork and review times. A national evidence record with unknown sample size or geography is not eligible for numeric publication. Existing legacy records with incomplete provenance remain historical rather than being falsely revalidated.

The current page hardcodes its Yiaga national-evidence panel in HTML. A separate small renderer change will be required to display later accepted JSON evidence and the review record. Producing valid JSON alone does not make a new source visible to users.
