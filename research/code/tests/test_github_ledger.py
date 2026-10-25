"""No-network central ledger persistence and paid-execution gate checks."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.budget import BudgetLedger, MINI, PersistenceError, envelope, new_ledger
from common.openai_client import OpenAIEditorialClient
from github_ledger import GitHubLedgerStore, OWNER, PATH

ENV = {"GITHUB_REPOSITORY": OWNER, "GITHUB_REF": "refs/heads/main",
       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "1234",
       "GH_TOKEN": "synthetic-not-a-real-token"}


class Response:
    def __init__(self, status=200, data=None):
        self.status_code, self.data = status, data
    def json(self):
        return deepcopy(self.data)


class Session:
    def __init__(self):
        self.headers, self.calls = {}, []
        self.state = new_ledger()
        self.put_status = 200
        self.get_status = 200
        self.ambiguous = False
        self.tamper = None
    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        raw = json.dumps(self.state).encode()
        data = {"type": "file", "encoding": "base64", "path": PATH, "size": len(raw),
                "content": base64.b64encode(raw).decode(), "sha": "a" * 40}
        if self.tamper:
            data = self.tamper(data)
        return Response(self.get_status, data)
    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        if self.put_status == 200:
            self.state = json.loads(base64.b64decode(kwargs["json"]["content"]))
        if self.ambiguous:
            raise TimeoutError("simulated timeout after remote commit")
        return Response(self.put_status)


class LedgerStoreTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.session = Session()
        self.store = GitHubLedgerStore(self.session)

    def test_read_and_durable_compare_swap_confirmation(self):
        initial, sha = self.store.read()
        ledger = BudgetLedger(initial, self.store.persist)
        ledger.reserve("run-123", "daily", envelope(MINI, 6000), {})
        self.assertEqual(self.session.state, ledger.state)
        self.assertEqual([x[0] for x in self.session.calls], ["GET", "GET", "PUT", "GET"])
        payload = self.session.calls[2][2]["json"]
        self.assertEqual(payload["sha"], sha)
        self.assertEqual(payload["branch"], "main")
        self.assertNotIn(ENV["GH_TOKEN"], payload["content"])
        for _, url, kwargs in self.session.calls:
            self.assertTrue(url.startswith("https://api.github.com/repos/" + OWNER))
            self.assertIs(kwargs["allow_redirects"], False)
        self.assertFalse(self.session.trust_env)

    def test_stale_revision_never_puts(self):
        ledger = BudgetLedger(self.store.read()[0], self.store.persist)
        self.session.state["revision"] = 1
        with self.assertRaises(PersistenceError):
            ledger.reserve("x", "daily", envelope(MINI, 6000), {})
        self.assertFalse(any(method == "PUT" for method, *_ in self.session.calls))

    def test_ambiguous_write_never_retried_and_does_not_permit_api(self):
        ledger = BudgetLedger(self.store.read()[0], self.store.persist)
        self.session.ambiguous = True
        class NeverSDK:
            def __init__(self): self.responses = self
            def create(self, **kwargs): raise AssertionError("Paid API was called")
        with self.assertRaises(PersistenceError):
            OpenAIEditorialClient(ledger, NeverSDK()).generate(
                request_id="x", kind="daily", prompt="Evidence", search=False,
                schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False})
        self.assertEqual(sum(method == "PUT" for method, *_ in self.session.calls), 1)
        self.assertEqual(self.session.state["entries"][0]["status"], "reserved")
        self.assertEqual(ledger.state["entries"], [])

    def test_missing_ledger_is_not_reset(self):
        self.session.get_status = 404
        with self.assertRaises(RuntimeError):
            self.store.read()
        self.assertFalse(any(method == "PUT" for method, *_ in self.session.calls))

    def test_malformed_redirect_wrong_path_or_encoding_rejected(self):
        changes = ({"type": "symlink"}, {"encoding": "none"}, {"path": "another.json"},
                   {"size": 1_000_001}, {"sha": "not-a-sha"}, {"content": "!invalid!"})
        for change in changes:
            self.session.tamper = lambda data: {**data, **change}
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.store.read()
        self.session.tamper = None
        self.session.get_status = 302
        with self.assertRaises(RuntimeError):
            self.store.read()

    def test_write_conflict_or_failure_does_not_proceed(self):
        for status in (302, 403, 409, 422, 500):
            self.session.put_status = status
            ledger = BudgetLedger(self.store.read()[0], self.store.persist)
            with self.subTest(status=status), self.assertRaises(PersistenceError):
                ledger.reserve("x", "daily", envelope(MINI, 6000), {})

    def test_untrusted_repository_branch_event_or_missing_token_rejected(self):
        for change in ({"GITHUB_REPOSITORY": "fork/repo"}, {"GITHUB_REF": "refs/heads/feature"},
                       {"GITHUB_EVENT_NAME": "pull_request_target"}, {"GH_TOKEN": ""}):
            with patch.dict(os.environ, {**ENV, **change}, clear=True):
                with self.subTest(change=change), self.assertRaises(RuntimeError):
                    GitHubLedgerStore(self.session)

    def test_activation_and_hard_limit_gate_precede_store_or_provider(self):
        from run_research import run
        for extra in ({}, {"AI_RESEARCH_ENABLED": "true"}):
            with patch.dict(os.environ, {**ENV, **extra}, clear=True), \
                    patch("run_research.GitHubLedgerStore", side_effect=AssertionError("store accessed")), \
                    patch("run_research.OpenAIEditorialClient", side_effect=AssertionError("API client accessed")):
                with self.assertRaises(RuntimeError):
                    run("daily", Path("."), Path("."), Path("."), Path("."))


if __name__ == "__main__":
    unittest.main()
