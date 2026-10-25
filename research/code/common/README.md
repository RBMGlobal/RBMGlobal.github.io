# OpenAI editorial access and shared US$10 budget

This package prepares validated JSON research output. It never publishes, sends
messages, executes model-produced code or refreshes local event data. No paid
request was used to test it. Python 3.12 is recommended. Install
`pip install -r common/requirements-lock.txt` for the tested dependency versions;
`requirements.txt` lists the two direct dependencies.

## Integration

```python
from common.budget import BudgetLedger
from common.openai_client import OpenAIEditorialClient

# Read the ONE central company-repository ledger; never create/reset it per run.
ledger = BudgetLedger(state, persist)
client = OpenAIEditorialClient(ledger)
result = client.generate(
    request_id="2026-10-25.daily.candidate.v1", kind="daily",
    prompt=source_evidence_and_task, schema=strict_json_schema,
    search=False,
)
candidate = result.data
```

`persist(new_state, expected_revision)` must compare-and-swap the central durable
ledger and return exactly `True` only after its remote commit is confirmed. A
runner-local write, queued upload or log is insufficient. It must reject a stale
revision rather than overwrite another job's spend. Use a shared concurrency
group and this CAS check. Initially commit `new_ledger()` once; a missing ledger
after activation is an error, never an invitation to reset spending.

`generate` arguments: `request_id`, `kind` (`daily`, `weekly`, `review`), `prompt`,
`schema`, optional `allowed_domains`, `search`, `max_search_calls`, and
`allow_review_fallback`. The prompt limit is 150 KiB UTF-8; the schema is 24 KiB and
must be a strict object schema with all properties required, no unknown fields,
and no external schema references. The result fields are `data`, `sources`,
`model`, `response_id`, `usage`, `max_search_calls`, `charged_microusd`.

Research uses the documented dated snapshot `gpt-5.4-mini-2026-03-17`. Daily search
has a ceiling of 2 calls, weekly 4. Before sending, the client can reduce the ceiling
to fit remaining funds; it never silently replaces required search with no search.
For collector-supplied evidence set `search=False`. Search returns a domain-checked
source list, but that does not establish publication dates or factual accuracy:
the separate source fetcher/validator must verify the actual documents.

Review uses `gpt-6.1-sol`, no tools and at most 4000 output tokens. Its official
model page currently lists no dated snapshot. If its reservation will not fit,
the default fallback is Mini; this is explicit in `result.model`. Set
`allow_review_fallback=False` to require Sol or stop. Research output is capped
at 6000 tokens; these caps include reasoning. Only web search is offered as a tool.

## Accounting and failure behavior

All money is integer micro-USD. The fixed monthly allowance is 10,000,000 micro-USD
(US$10), on UTC calendar months. Settlement and reservations share one ledger
across daily/weekly jobs. The client durably reserves before calling the provider.
No ambiguous timeout/network/500 outcome is retried. SDK automatic retries are
disabled; its OIDC authentication handler may refresh an explicitly rejected 401
token, which is an authentication retry rather than an ambiguous paid response.
Reusing a request ID is forbidden even after settlement, so resumed runs cannot
silently resend a charged operation.

Reservation is conservative: the model's **entire maximum input window for every
possible inference round** (search calls+1), output cap, and every permitted
search fee. It does not assume a numerical search-result cap: OpenAI exposes only
`return_token_budget=default|unlimited`, not a caller-specified token count.

| Request | Reservation before call |
|---|---:|
| Mini, no search, 6000 output | US$0.231 |
| Mini, 2 searches, 6000 output | US$0.659 |
| Mini, 4 searches, 6000 output | US$1.087 |
| Sol review, 4000 output | US$5.310 |

These are temporary upper bounds, **not expected costs**. Valid terminal usage
replaces the reservation with a conservative charge using uncached rates and the
full permitted search fee. Sol settlement uses its highest documented
long-context/cache-write rates, even for shorter prompts, rather than assuming
discounts. The ledger is an upper-bound spending guard, not an invoice. Exact
invoice reconciliation may reduce settled bounds manually after verification.

Missing/malformed usage, unexpected models/tiers/tools, provider errors, a month
boundary during a call, or failed settlement persistence retain reservations.
Old unresolved reservations continue to encumber later months until reconciled;
there is no automatic expiry. Never delete entries just to make a failed run retry.
Budget/collection/provider failure preserves the last published assessment and
cannot be described as a verified no-change review.

Configure a **dedicated OpenAI API project with its enforced monthly hard limit
set to US$10**, approved model access, and no other workloads. This external control
is required before activation. Provider enforcement may lag slightly; no local
ledger can guarantee the provider's invoice against price changes, incorrect
provider usage or delayed enforcement. The ledger refuses new calls before its
verified reservation would exceed US$10. The tariffs and context limits were checked
on 2026-10-25; review this policy before changing a model, endpoint, tier or pricing.
API charges are separate from any ChatGPT subscription and may exclude taxes.

## Authentication

Preferred: configure OpenAI workload federation for GitHub Actions, mapping exact
repository/main/workflow claims to a dedicated API project service account. The
research job requires `id-token: write` and `contents: read`; only a separate
controlled ledger writer needs repository write permission. Set Actions variables
`OPENAI_WIF_AUDIENCE`, `OPENAI_IDENTITY_PROVIDER_ID`,
`OPENAI_SERVICE_ACCOUNT_ID` and optionally `OPENAI_PROJECT_ID`. GitHub supplies
the short-lived request URL/token. The official SDK performs the documented
exchange; this package does not invent an exchange endpoint or persist tokens.

Fallback: put a project-scoped service-account key in Actions secret
`OPENAI_API_KEY` and its project ID in variable `OPENAI_PROJECT_ID`. A partial or
failed OIDC setup never silently falls back. Keep credentials out of payloads,
ledgers, logs, evidence archives and public site output. Ambient admin credentials
and custom authorization headers are rejected. The API endpoint and standard tier
are fixed; regional/priority/ultrafast pricing cannot be enabled by caller input.

## Offline verification

`python -m unittest discover -s common/tests -v`

Tests cover CAS ordering/conflicts, cap enforcement, restarts and duplicate IDs,
retained uncertain reservations, month rollover, source-only requests, affordable
search/model adaptation, strict schemas, source-domain checks, model/tier/tool
rejection, real SDK 500 no-retry behavior using a mock transport, and OIDC/key
construction without network. They do not verify a real project's billing limit,
OIDC mapping, entitlement, model quality or a paid response.

## Official references, verified 2026-10-25

- [Mini snapshot, context and prices](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
- [Sol context, alias and prices](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [Responses output/tool-call limits](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Web search, domains and returned-token control](https://developers.openai.com/api/docs/guides/tools-web-search)
- [Search pricing](https://developers.openai.com/api/docs/pricing)
- [GitHub Actions OIDC setup](https://developers.openai.com/api/docs/guides/workload-identity-federation/github-actions)
- [Project hard-limit controls](https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform)
- [Official SDK 3.37.0](https://pypi.org/project/openai/3.37.0/)
