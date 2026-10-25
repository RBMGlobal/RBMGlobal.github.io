"""Fail-closed, integer-microdollar accounting. This module never uses the network.

The caller owns the ONE central ledger and its concurrency/CAS persistence. A
reservation is durable before a paid request. Unknown outcomes remain reserved;
settlement is an upper bound at verified tariffs, not an invoice reconciliation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import re

MONTHLY_LIMIT_MICROUSD = 10_000_000
OWNER = "RBMGlobal/RBMGlobal.github.io"
PRICING_AS_OF = "2026-10-25"
MINI = "gpt-5.4-mini-2026-03-17"
SOL = "gpt-6.1-sol"
# Per-million USD is numerically equal to micro-USD per token. Use uncached
# rates, and Sol's highest long-context/cache-write rates even on small inputs.
TARIFFS = {
    MINI: {"input": "0.75", "output": "4.5", "max_input": 272_000},
    SOL: {"input": "5", "output": "15", "max_input": 1_050_000},
}
SEARCH_MICROUSD = 10_000


class BudgetError(RuntimeError):
    pass


class BudgetExceeded(BudgetError):
    pass


class DuplicateRequest(BudgetError):
    pass


class PersistenceError(BudgetError):
    pass


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise BudgetError(f"Invalid {name}")
    return value


def _utc(now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise BudgetError("An aware timestamp is required")
    return now.astimezone(timezone.utc)


def stamp(now=None):
    return _utc(now).isoformat(timespec="seconds").replace("+00:00", "Z")


def token_cost(model, input_tokens, output_tokens, search_calls):
    if model not in TARIFFS:
        raise BudgetError("Unreviewed model/tariff")
    for name, val in (("input tokens", input_tokens), ("output tokens", output_tokens),
                      ("search calls", search_calls)):
        _integer(val, name)
    price = TARIFFS[model]
    amount = (Decimal(price["input"]) * input_tokens
              + Decimal(price["output"]) * output_tokens
              + SEARCH_MICROUSD * search_calls)
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def envelope(model, max_output_tokens, max_search_calls=0):
    """Reserve full model input context for EVERY possible inference round.

    A hosted tool call may require another model inference. max_tool_calls=N
    permits N+1 rounds; charging the full input window each time avoids relying
    on an unpublished search-content budget or token-count estimates. Output
    cap includes reasoning tokens across the response. No discounts assumed.
    """
    if model not in TARIFFS:
        raise BudgetError("Unreviewed model/tariff")
    _integer(max_output_tokens, "output limit", 1)
    _integer(max_search_calls, "search limit")
    if max_output_tokens > 6000 or max_search_calls > 4:
        raise BudgetError("Request exceeds reviewed envelope")
    if model == SOL and max_search_calls:
        raise BudgetError("Sol is limited to source-only review")
    inputs = TARIFFS[model]["max_input"] * (max_search_calls + 1)
    return {
        "model": model, "max_output_tokens": max_output_tokens,
        "max_search_calls": max_search_calls, "max_input_tokens": inputs,
        "reserved_microusd": token_cost(model, inputs, max_output_tokens, max_search_calls),
        "pricing_as_of": PRICING_AS_OF,
    }


def new_ledger():
    return {"schema_version": 1, "owner": OWNER, "currency": "USD",
            "monthly_limit_microusd": MONTHLY_LIMIT_MICROUSD,
            "revision": 0, "entries": []}


def validate_ledger(state):
    if not isinstance(state, dict) or set(state) != set(new_ledger()):
        raise BudgetError("Invalid ledger structure")
    for key in ("schema_version", "owner", "currency", "monthly_limit_microusd"):
        if state[key] != new_ledger()[key]:
            raise BudgetError(f"Unexpected ledger {key}")
    _integer(state["revision"], "revision")
    if not isinstance(state["entries"], list):
        raise BudgetError("Invalid ledger entries")
    seen = set()
    for entry in state["entries"]:
        if not isinstance(entry, dict):
            raise BudgetError("Invalid ledger entry")
        required = {"request_id", "month", "created_at", "kind", "status", "request_sha256", "envelope"}
        optional = {"charged_microusd", "usage", "response_id", "settled_at", "reason"}
        if not required <= entry.keys() or entry.keys() - required - optional:
            raise BudgetError("Invalid ledger entry fields")
        rid = entry["request_id"]
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", rid) or rid in seen:
            raise BudgetError("Invalid or duplicate request identifier")
        seen.add(rid)
        if entry["kind"] not in ("daily", "weekly", "review"):
            raise BudgetError("Invalid request kind")
        if entry["status"] not in ("reserved", "uncertain", "settled"):
            raise BudgetError("Invalid reservation status")
        try:
            created = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00"))
            if _utc(created).strftime("%Y-%m") != entry["month"]:
                raise ValueError()
        except (TypeError, ValueError, AttributeError):
            raise BudgetError("Invalid ledger date") from None
        if not re.fullmatch(r"[0-9a-f]{64}", entry["request_sha256"]):
            raise BudgetError("Invalid request digest")
        env = entry["envelope"]
        try:
            if env != envelope(env["model"], env["max_output_tokens"], env["max_search_calls"]):
                raise BudgetError("Unreviewed reservation envelope")
        except (KeyError, TypeError):
            raise BudgetError("Invalid reservation envelope") from None
        if entry["status"] == "settled":
            _integer(entry.get("charged_microusd"), "settled amount")
            if entry["charged_microusd"] > env["reserved_microusd"]:
                raise BudgetError("Settlement exceeds reservation")
    return state


def committed_microusd(state, now=None):
    validate_ledger(state)
    month = _utc(now).strftime("%Y-%m")
    total = 0
    for entry in state["entries"]:
        # Unknown old outcomes also encumber future months until reconciled.
        if entry["status"] != "settled":
            total += entry["envelope"]["reserved_microusd"]
        elif entry["month"] == month:
            total += entry["charged_microusd"]
    return total


def reserve(state, request_id, kind, limits, request_sha256, now=None):
    validate_ledger(state)
    if any(e["request_id"] == request_id for e in state["entries"]):
        raise DuplicateRequest("This request identifier was already reserved; do not resend it")
    if limits["reserved_microusd"] + committed_microusd(state, now) > MONTHLY_LIMIT_MICROUSD:
        raise BudgetExceeded("Shared monthly US$10 budget cannot cover this reservation")
    candidate = deepcopy(state)
    candidate["entries"].append({
        "request_id": request_id, "kind": kind, "month": _utc(now).strftime("%Y-%m"),
        "created_at": stamp(now), "status": "reserved", "envelope": deepcopy(limits),
        "request_sha256": request_sha256,
    })
    candidate["revision"] += 1
    return validate_ledger(candidate)


def settle(state, request_id, usage, response_id, now=None):
    """Settle a known terminal response at a conservative usage-based upper bound."""
    candidate = deepcopy(validate_ledger(state))
    found = [e for e in candidate["entries"] if e["request_id"] == request_id]
    if len(found) != 1 or found[0]["status"] != "reserved":
        raise BudgetError("Only a reserved request can settle automatically")
    entry = found[0]
    limits = entry["envelope"]
    if not isinstance(response_id, str) or not re.fullmatch(r"resp_[A-Za-z0-9_-]{1,200}", response_id):
        raise BudgetError("Invalid provider response identifier")
    if not isinstance(usage, dict) or set(usage) not in ({"input_tokens", "output_tokens"}, {"input_tokens", "output_tokens", "web_actions"}):
        raise BudgetError("Incomplete usage")
    inputs = _integer(usage["input_tokens"], "usage input")
    outputs = _integer(usage["output_tokens"], "usage output")
    if inputs > limits["max_input_tokens"] or outputs > limits["max_output_tokens"]:
        raise BudgetError("Provider usage exceeds reviewed envelope")
    # Charge every returned web action conservatively, including opens/failed attempts.
    # More output items than requested do not invalidate otherwise verified usage.
    # The precommitted monetary reservation is still an absolute settlement ceiling.
    actions = _integer(usage.get("web_actions", limits["max_search_calls"]), "web actions")
    if actions and not limits["max_search_calls"]:
        raise BudgetError("Unexpected tool use on a source-only request")
    amount = token_cost(limits["model"], inputs, outputs, max(actions, limits["max_search_calls"]))
    if amount > limits["reserved_microusd"]:
        raise BudgetError("Provider usage exceeds reserved amount")
    entry.update(status="settled", charged_microusd=amount, usage=dict(usage),
                 response_id=response_id, settled_at=stamp(now))
    candidate["revision"] += 1
    return validate_ledger(candidate)


def mark_uncertain(state, request_id, reason="provider_outcome_unknown"):
    if reason not in ("provider_outcome_unknown", "usage_unverifiable", "cross_month"):
        raise BudgetError("Unrecognised uncertainty reason")
    candidate = deepcopy(validate_ledger(state))
    found = [e for e in candidate["entries"] if e["request_id"] == request_id]
    if len(found) != 1 or found[0]["status"] != "reserved":
        raise BudgetError("Unknown or already terminal reservation")
    found[0].update(status="uncertain", reason=reason)
    candidate["revision"] += 1
    return candidate


class BudgetLedger:
    """persist(new_state, expected_revision) MUST CAS the central durable ledger.

    Return True only after the remote commit is confirmed. Never provide a
    callback that merely writes a runner-local file or defers persistence.
    """
    def __init__(self, state, persist, clock=None):
        self.state = deepcopy(validate_ledger(state))
        self.persist = persist
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _commit(self, candidate):
        expected = self.state["revision"]
        try:
            confirmed = self.persist(deepcopy(candidate), expected)
        except Exception:
            raise PersistenceError("Central ledger persistence failed; no new paid call is permitted") from None
        if confirmed is not True:
            raise PersistenceError("Central ledger persistence was not confirmed")
        self.state = candidate

    def remaining(self):
        return max(0, MONTHLY_LIMIT_MICROUSD - committed_microusd(self.state, self.clock()))

    def reserve(self, request_id, kind, limits, payload):
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
        self._commit(reserve(self.state, request_id, kind, limits, digest, self.clock()))

    def settle(self, request_id, usage, response_id):
        entry = next(e for e in self.state["entries"] if e["request_id"] == request_id)
        if entry["month"] != _utc(self.clock()).strftime("%Y-%m"):
            self.uncertain(request_id, "cross_month")
            raise BudgetError("Month-boundary response retained for reconciliation")
        self._commit(settle(self.state, request_id, usage, response_id, self.clock()))

    def uncertain(self, request_id, reason="provider_outcome_unknown"):
        self._commit(mark_uncertain(self.state, request_id, reason))
