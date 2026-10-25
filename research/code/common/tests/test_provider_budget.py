from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from common.budget import (BudgetLedger, BudgetError, BudgetExceeded, DuplicateRequest,
                           PersistenceError, MINI, SOL, committed_microusd,
                           envelope, new_ledger, reserve, settle, validate_ledger)
from common.openai_client import (OpenAIEditorialClient, ClientError, InvalidResponse,
                                  ProviderOutcomeUnknown, sdk_from_env, oidc_provider)

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SCHEMA = {"type": "object", "properties": {"changed": {"type": "boolean"}},
          "required": ["changed"], "additionalProperties": False}


class Store:
    def __init__(self):
        self.state = new_ledger()
        self.calls = []
        self.fail = False

    def persist(self, state, revision):
        self.calls.append(deepcopy(state))
        if self.fail or revision != self.state["revision"]:
            raise RuntimeError("simulated CAS conflict")
        self.state = deepcopy(state)
        return True

    def ledger(self):
        return BudgetLedger(self.state, self.persist, lambda: NOW)


def response(payload):
    items = []
    if payload["tools"]:
        items.append({"type": "web_search_call", "status": "completed", "action": {
            "type": "search", "sources": [{"url": "https://afrobarometer.org/release", "title": "Release"}]}})
    items.append({"type": "message", "status": "completed", "role": "assistant",
                  "content": [{"type": "output_text", "text": '{"changed":false}', "annotations": []}]})
    return {"id": "resp_test", "status": "completed", "model": payload["model"],
            "service_tier": "default", "usage": {"input_tokens": 1000, "output_tokens": 100},
            "output": items}


class FakeSDK:
    def __init__(self, store=None, modify=None):
        self.responses = self
        self.calls = []
        self.store = store
        self.modify = modify

    def create(self, **payload):
        if self.store:
            assert self.store.state["entries"][-1]["status"] == "reserved"
        self.calls.append(deepcopy(payload))
        result = response(payload)
        return self.modify(result) if self.modify else result


def run(client, **kwargs):
    return client.generate(request_id="daily.2026-10-25.discovery", kind="daily",
                           prompt="Find authoritative Nigeria polling", schema=SCHEMA,
                           allowed_domains=["afrobarometer.org"], **kwargs)


class BudgetTests(unittest.TestCase):
    def test_envelopes_include_every_input_round_and_reasoning_output(self):
        self.assertEqual(envelope(MINI, 6000, 2)["reserved_microusd"], 659000)
        self.assertEqual(envelope(MINI, 6000, 4)["reserved_microusd"], 1087000)
        self.assertEqual(envelope(SOL, 4000)["reserved_microusd"], 5310000)

    def test_exact_cap_and_one_micro_over(self):
        state = new_ledger()
        limits = envelope(MINI, 6000)
        for i in range(43):
            state = reserve(state, str(i), "daily", limits, "a" * 64, NOW)
        with self.assertRaises(BudgetExceeded):
            reserve(state, "44", "daily", limits, "a" * 64, NOW)
        self.assertEqual(len(state["entries"]), 43)

    def test_settlement_returns_unused_reserve_but_keeps_full_tool_fee(self):
        state = reserve(new_ledger(), "one", "daily", envelope(MINI, 6000, 2), "a" * 64, NOW)
        result = settle(state, "one", {"input_tokens": 1000, "output_tokens": 100}, "resp_x", NOW)
        self.assertEqual(result["entries"][0]["charged_microusd"], 21200)
        self.assertEqual(state["entries"][0]["status"], "reserved")

    def test_duplicate_cannot_be_resent_after_restart(self):
        store = Store()
        store.ledger().reserve("same", "daily", envelope(MINI, 6000), {})
        with self.assertRaises(DuplicateRequest):
            store.ledger().reserve("same", "daily", envelope(MINI, 6000), {})

    def test_prior_month_unresolved_stays_reserved(self):
        state = reserve(new_ledger(), "one", "daily", envelope(MINI, 6000), "a" * 64, NOW)
        nov = datetime(2026, 11, 1, tzinfo=timezone.utc)
        self.assertEqual(committed_microusd(state, nov), 231000)
        state = settle(state, "one", {"input_tokens": 1000, "output_tokens": 100}, "resp_x", NOW)
        self.assertEqual(committed_microusd(state, nov), 0)

    def test_month_crossing_does_not_release_uncertain_month_charge(self):
        store = Store()
        ledger = store.ledger()
        ledger.reserve("month", "daily", envelope(MINI, 6000), {})
        ledger.clock = lambda: datetime(2026, 11, 1, tzinfo=timezone.utc)
        with self.assertRaises(BudgetError):
            ledger.settle("month", {"input_tokens": 100, "output_tokens": 10}, "resp_x")
        self.assertEqual(store.state["entries"][0]["status"], "uncertain")

    def test_ledger_rejects_bad_currency_limit_negative_values_and_tariff(self):
        for key, value in (("monthly_limit_microusd", 20000000), ("currency", "GBP"),
                           ("revision", -1), ("owner", "another/repo")):
            state = new_ledger()
            state[key] = value
            with self.subTest(key=key), self.assertRaises(BudgetError):
                validate_ledger(state)
        for bad in (-1, 1.2, True):
            with self.assertRaises(BudgetError):
                envelope(MINI, bad)
        with self.assertRaises(BudgetError):
            envelope("made-up-model", 4000)

    def test_compare_and_swap_stale_ledger_fails_without_mutating_local_state(self):
        store = Store()
        a, b = store.ledger(), store.ledger()
        a.reserve("a", "daily", envelope(MINI, 6000), {})
        with self.assertRaises(PersistenceError):
            b.reserve("b", "daily", envelope(MINI, 6000), {})
        self.assertEqual(b.state["revision"], 0)
        self.assertEqual(store.state["entries"][0]["request_id"], "a")

    def test_unconfirmed_callback_is_not_durability(self):
        ledger = BudgetLedger(new_ledger(), lambda *_: None, lambda: NOW)
        with self.assertRaises(PersistenceError):
            ledger.reserve("a", "daily", envelope(MINI, 6000), {})
        self.assertEqual(ledger.state["revision"], 0)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.sdk = FakeSDK(self.store)
        self.client = OpenAIEditorialClient(self.store.ledger(), self.sdk)

    def test_reservation_precedes_network_and_strict_web_contract(self):
        result = run(self.client)
        self.assertEqual(result.data, {"changed": False})
        payload = self.sdk.calls[0]
        self.assertEqual(payload["tools"][0]["type"], "web_search")
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual(payload["max_tool_calls"], 2)
        self.assertEqual(payload["text"]["format"]["strict"], True)
        self.assertEqual(payload["model"], MINI)
        self.assertFalse(payload["store"])
        self.assertEqual(len(self.sdk.calls), 1)
        self.assertEqual(self.store.state["entries"][0]["status"], "settled")

    def test_durable_write_failure_prevents_paid_request(self):
        self.store.fail = True
        with self.assertRaises(PersistenceError):
            run(self.client)
        self.assertEqual(self.sdk.calls, [])

    def test_timeout_is_never_retried_and_keeps_full_reservation(self):
        def timeout(_):
            raise TimeoutError("contains private request")
        sdk = FakeSDK(self.store, timeout)
        client = OpenAIEditorialClient(self.store.ledger(), sdk)
        with self.assertRaises(ProviderOutcomeUnknown) as raised:
            run(client)
        self.assertNotIn("private request", str(raised.exception))
        self.assertEqual(len(sdk.calls), 1)
        self.assertEqual(self.store.state["entries"][0]["status"], "uncertain")
        self.assertEqual(client.ledger.remaining(), 10000000 - 659000)

    def test_incomplete_schema_is_rejected_but_known_usage_is_accounted(self):
        sdk = FakeSDK(self.store, lambda r: {**r, "status": "incomplete"})
        with self.assertRaises(InvalidResponse):
            run(OpenAIEditorialClient(self.store.ledger(), sdk))
        self.assertEqual(self.store.state["entries"][0]["status"], "settled")

    def test_missing_or_over_envelope_usage_never_releases_reservation(self):
        for usage in (None, {}, {"input_tokens": -1, "output_tokens": 1},
                      {"input_tokens": 10, "output_tokens": 6001}):
            store = Store()
            sdk = FakeSDK(store, lambda r: {**r, "usage": usage})
            with self.subTest(usage=usage), self.assertRaises(ProviderOutcomeUnknown):
                run(OpenAIEditorialClient(store.ledger(), sdk))
            self.assertEqual(store.state["entries"][0]["status"], "uncertain")

    def test_review_is_source_only_and_uses_sol(self):
        result = self.client.generate(request_id="review", kind="review", prompt="Review supplied evidence",
                                      schema=SCHEMA)
        self.assertEqual(result.model, SOL)
        self.assertEqual(self.sdk.calls[0]["tools"], [])
        self.assertEqual(self.sdk.calls[0]["max_output_tokens"], 4000)

    def test_source_only_candidate_and_large_bounded_evidence_input(self):
        result = run(self.client, search=False)
        self.assertEqual(result.max_search_calls, 0)
        self.assertEqual(self.sdk.calls[0]["tool_choice"], "none")
        self.client.generate(request_id="large", kind="daily", prompt="a" * 150000,
                             schema=SCHEMA, search=False)
        with self.assertRaises(ClientError):
            self.client.generate(request_id="too-large", kind="daily", prompt="é" * 100000,
                                 schema=SCHEMA, search=False)

    def test_affordable_review_fallback_is_disclosed(self):
        ledger = self.client.ledger
        for i in range(21):
            ledger.reserve(f"uncertain-{i}", "daily", envelope(MINI, 6000), {})
        result = self.client.generate(request_id="review", kind="review", prompt="Review evidence", schema=SCHEMA)
        self.assertEqual(result.model, MINI)

    def test_affordable_search_cap_reduced_before_call(self):
        ledger = self.client.ledger
        for i in range(41):
            ledger.reserve(f"uncertain-{i}", "daily", envelope(MINI, 6000), {})
        result = run(self.client)
        self.assertEqual(result.max_search_calls, 1)
        self.assertEqual(len(self.sdk.calls), 1)

    def test_no_money_means_no_paid_call_not_source_free_claim(self):
        for i in range(43):
            self.client.ledger.reserve(f"uncertain-{i}", "daily", envelope(MINI, 6000), {})
        with self.assertRaises(BudgetExceeded):
            run(self.client)
        self.assertEqual(self.sdk.calls, [])

    def test_rejects_external_schema_before_paid_request(self):
        schema = {**SCHEMA, "properties": {"changed": {"$ref": "https://bad.example/schema"}}}
        with self.assertRaises(ClientError):
            self.client.generate(request_id="x", kind="daily", prompt="x", schema=schema, search=False)
        self.assertEqual(self.sdk.calls, [])

    def test_escaped_domain_lookalike_is_not_approved(self):
        def bad(r):
            r["output"][0]["action"]["sources"][0]["url"] = "https://afrobarometer.org.evil.example/story"
            return r
        with self.assertRaises(InvalidResponse):
            run(OpenAIEditorialClient(self.store.ledger(), FakeSDK(self.store, bad)))

    def test_unknown_tools_missing_search_duplicate_json_and_schema_fail(self):
        def unknown(r):
            r["output"].append({"type": "function_call"})
            return r
        def missing(r):
            r["output"] = r["output"][1:]
            return r
        def duplicate(r):
            r["output"][-1]["content"][0]["text"] = '{"changed":false,"changed":true}'
            return r
        def wrong_type(r):
            r["output"][-1]["content"][0]["text"] = '{"changed":"no"}'
            return r
        store = Store()
        with self.assertRaises(ProviderOutcomeUnknown):
            run(OpenAIEditorialClient(store.ledger(), FakeSDK(store, unknown)))
        self.assertEqual(store.state["entries"][0]["status"], "uncertain")
        for modify in (missing, duplicate, wrong_type):
            store = Store()
            with self.subTest(modify=modify), self.assertRaises(InvalidResponse):
                run(OpenAIEditorialClient(store.ledger(), FakeSDK(store, modify)))

    def test_unapproved_tier_or_model_keeps_full_reservation(self):
        for changes in ({"model": "other-model"}, {"service_tier": "ultrafast"}):
            store = Store()
            with self.assertRaises(ProviderOutcomeUnknown):
                run(OpenAIEditorialClient(store.ledger(), FakeSDK(store, lambda r: {**r, **changes})))
            self.assertEqual(store.state["entries"][0]["status"], "uncertain")

    def test_accounting_failure_identifies_each_rejected_guard_without_relaxing_it(self):
        cases = (
            ({"status": "queued"}, "terminal_status"),
            ({"model": "gpt-5.4-mini"}, "expected_model"),
            ({"service_tier": "flex"}, "budgeted_service_tier"),
            ({"usage": None}, "usage_object"),
            ({"output": [{"type": "function_call"}]}, "approved_output_types"),
        )
        for change, expected in cases:
            store = Store()
            sdk = FakeSDK(store, lambda r: {**r, **change})
            with self.subTest(expected=expected), self.assertRaises(ProviderOutcomeUnknown) as raised:
                run(OpenAIEditorialClient(store.ledger(), sdk))
            diagnostic = json.loads(str(raised.exception).split("; safe metadata: ")[1])
            self.assertEqual(diagnostic["failed_checks"], [expected])
            self.assertEqual(store.state["entries"][0]["status"], "uncertain")
            self.assertEqual(committed_microusd(store.state, NOW), 659000)
            self.assertEqual(len(sdk.calls), 1)

    def test_extra_web_actions_settle_only_within_precommitted_money(self):
        def extra(r):
            r["output"] = [deepcopy(r["output"][0]) for _ in range(3)] + r["output"][1:]
            return r
        result = run(OpenAIEditorialClient(self.store.ledger(), FakeSDK(self.store, extra)))
        self.assertEqual(result.charged_microusd, 31200)
        self.assertEqual(result.usage["web_actions"], 3)
        self.assertEqual(self.store.state["entries"][0]["envelope"]["reserved_microusd"], 659000)

    def test_open_find_and_failed_actions_are_charged_conservatively(self):
        def mixed(r):
            r["output"][1:1] = [
                {"type": "web_search_call", "status": "completed", "action": {"type": "open_page"}},
                {"type": "web_search_call", "status": "completed", "action": {"type": "find_in_page"}},
                {"type": "web_search_call", "status": "failed", "action": {}},
            ]
            return r
        result = run(OpenAIEditorialClient(self.store.ledger(), FakeSDK(self.store, mixed)))
        self.assertEqual(result.charged_microusd, 41200)
        self.assertEqual(len(result.sources), 1)

    def test_extra_actions_cannot_exceed_reserved_money(self):
        def over(r):
            r["output"] = [deepcopy(r["output"][0]) for _ in range(3)] + r["output"][1:]
            r["usage"] = {"input_tokens": 816000, "output_tokens": 6000}
            return r
        with self.assertRaises(ProviderOutcomeUnknown):
            run(OpenAIEditorialClient(self.store.ledger(), FakeSDK(self.store, over)))
        self.assertEqual(self.store.state["entries"][0]["status"], "uncertain")
        self.assertEqual(committed_microusd(self.store.state, NOW), 659000)

    def test_diagnostic_never_echoes_untrusted_provider_strings(self):
        private = "Bearer synthetic-private-token https://private.example/article"
        changed = {"status": private, "model": private, "service_tier": private,
                   "id": private, "usage": {"input_tokens": private, "output_tokens": True},
                   "output": [{"type": private, "text": private}, private]}
        sdk = FakeSDK(self.store, lambda r: {**r, **changed})
        with self.assertRaises(ProviderOutcomeUnknown) as raised:
            run(OpenAIEditorialClient(self.store.ledger(), sdk))
        message = str(raised.exception)
        self.assertNotIn(private, message)
        self.assertNotIn("private.example", message)
        diagnostic = json.loads(message.split("; safe metadata: ")[1])
        self.assertEqual(diagnostic["model"], "unrecognized")
        self.assertEqual(diagnostic["output_counts"]["unrecognized"], 1)
        self.assertEqual(diagnostic["output_counts"]["malformed"], 1)
        self.assertFalse(diagnostic["token_checks"]["input_tokens_valid"])
        self.assertFalse(diagnostic["token_checks"]["output_tokens_valid"])

    def test_invalid_usage_reports_flags_and_keeps_full_reservation(self):
        sdk = FakeSDK(self.store, lambda r: {**r, "usage": {"input_tokens": 1, "output_tokens": 6001}})
        with self.assertRaises(ProviderOutcomeUnknown) as raised:
            run(OpenAIEditorialClient(self.store.ledger(), sdk))
        diagnostic = json.loads(str(raised.exception).split("; safe metadata: ")[1])
        self.assertEqual(diagnostic["failed_checks"], [])
        self.assertTrue(diagnostic["token_checks"]["input_tokens_valid"])
        self.assertFalse(diagnostic["token_checks"]["output_tokens_valid"])
        self.assertEqual(committed_microusd(self.store.state, NOW), 659000)

    def test_unconfirmed_settlement_stops_without_second_ledger_mutation(self):
        for remote_committed in (False, True):
            store = Store()
            def persist(state, revision):
                if len(store.calls) == 1:
                    store.calls.append(deepcopy(state))
                    if remote_committed:
                        store.state = deepcopy(state)
                    raise RuntimeError("settlement confirmation lost")
                return store.persist(state, revision)
            ledger = BudgetLedger(store.state, persist, lambda: NOW)
            sdk = FakeSDK(store)
            with self.subTest(remote_committed=remote_committed), self.assertRaises(ProviderOutcomeUnknown) as raised:
                run(OpenAIEditorialClient(ledger, sdk))
            self.assertIn("Settlement persistence was not confirmed", str(raised.exception))
            self.assertEqual(len(store.calls), 2)
            self.assertEqual(len(sdk.calls), 1)
            self.assertEqual(ledger.state["entries"][0]["status"], "reserved")
            self.assertEqual(ledger.remaining(), 10000000 - 659000)
            self.assertEqual(store.state["entries"][0]["status"], "settled" if remote_committed else "reserved")

    def test_sdk_pinned_endpoint_zero_retries_and_no_network_on_construction(self):
        sdk = sdk_from_env({"OPENAI_API_KEY": "test-not-a-real-key", "OPENAI_PROJECT_ID": "proj_test"})
        self.assertEqual(sdk.max_retries, 0)
        self.assertEqual(str(sdk.base_url), "https://api.openai.com/v1/")
        sdk.close()

    def test_oidc_preferred_complete_configuration_without_network(self):
        env = {"OPENAI_WIF_AUDIENCE": "https://api.openai.com/v1", "OPENAI_IDENTITY_PROVIDER_ID": "ip_test",
               "OPENAI_SERVICE_ACCOUNT_ID": "sa_test", "OPENAI_API_KEY": "not-used",
               "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.githubusercontent.com/request",
               "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "test-runtime-token"}
        sdk = sdk_from_env(env)
        self.assertIsNotNone(sdk.workload_identity)
        sdk.close()
        del env["OPENAI_SERVICE_ACCOUNT_ID"]
        with self.assertRaises(ClientError):
            sdk_from_env(env)

    def test_oidc_rejects_credential_exfiltration_host(self):
        with self.assertRaises(ClientError):
            oidc_provider("aud", {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://evil.example/request",
                                  "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "private"})

    def test_actual_sdk_does_not_retry_ambiguous_500(self):
        import httpx2
        from openai import OpenAI
        calls = []
        def fail(request):
            calls.append(request)
            return httpx2.Response(500, json={"error": {"message": "simulated", "type": "server_error"}})
        sdk = OpenAI(api_key="not-real", max_retries=0,
                     http_client=httpx2.Client(transport=httpx2.MockTransport(fail)))
        with self.assertRaises(ProviderOutcomeUnknown):
            run(OpenAIEditorialClient(self.store.ledger(), sdk))
        sdk.close()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.state["entries"][0]["status"], "uncertain")


if __name__ == "__main__":
    unittest.main()
