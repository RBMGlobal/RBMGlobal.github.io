"""No-network integration of discovery -> collection -> candidate -> reviewer -> adapter.

All publisher/model content is a synthetic fixture. These tests never instantiate
the paid client or access GitHub. Real deterministic sentiment validation runs.
"""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_research as runner
from sentiment.apply_review import apply_review, InvalidReview

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sentiment_test_fixtures", HERE / "sentiment/tests/fixtures.py")
fixtures = importlib.util.module_from_spec(spec)
# The standalone fixture also supports running directly in its own directory.
sys.path.insert(0, str(HERE / "sentiment"))
spec.loader.exec_module(fixtures)
sys.path.pop(0)


class FixedClock(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return fixtures.NOW.astimezone(tz or dt.timezone.utc)


class DailyOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packet = fixtures.fixture(self.root / "fixture")
        self.repo = self.root / "repo"
        (self.repo / "sentiment").mkdir(parents=True)
        shutil.copyfile(self.root / "fixture/baseline.json", self.repo / "sentiment/data.json")
        self.config = self.root / "config"
        (self.config / "sentiment/examples").mkdir(parents=True)
        shutil.copyfile(HERE / "sentiment/candidate.schema.json", self.config / "sentiment/candidate.schema.json")
        policy = dict(self.packet["policy"], monthly_limit_usd=10, max_sources=10,
                      max_model_evidence_chars=65000, publishers={"primary.example.test": "Example Research"})
        (self.config / "policy.json").write_text(json.dumps(policy))
        self.work, self.output = self.root / "work", self.root / "output"
        self.work.mkdir(); self.output.mkdir()
        self.calls, self.bad_candidate, self.disagree, self.collect_failure = [], False, False, False
        self.initial_collect_failure = False
        self.blocked_domain = 'blocked.example.test'
        self.recovery_budget_failure = False

    def fake_collect(self, proposals, policy, output, urls):
        if self.collect_failure or (self.initial_collect_failure and output.name == 'sources'):
            runner.write_json(output / 'collection.json', {'accepted': 0, 'rejected': [
                {'url': 'https://' + self.blocked_domain + '/report', 'reason': '403 Client Error'}]})
            raise ValueError("No source passed collection and publication-date checks")
        shutil.copytree(self.root / "fixture", output)
        return copy.deepcopy(self.packet["manifest"])

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        role = kwargs["request_id"].split(":")[-1]
        if role == 'source-recovery' and self.recovery_budget_failure:
            raise RuntimeError('Synthetic budget reservation refused')
        if role in ("discovery", "source-recovery"):
            data = {"sources": [{"url": "https://primary.example.test/survey.pdf"}], "limitations": "Synthetic bounded discovery"}
        elif role == "candidate":
            data = copy.deepcopy(self.packet["candidate"])
            data["review_scope"] = "Only supplied fixture evidence was reviewed; this is not exhaustive."
            if self.bad_candidate:
                data["baseline_sha256"] = "0" * 64
        elif role == "review":
            data = {"approved": not self.disagree, "issues": ["Synthetic contradiction"] if self.disagree else [], "checks": {key: not self.disagree for key in runner.REVIEW["properties"]["checks"]["properties"]}}
        else:
            raise AssertionError(role)
        return SimpleNamespace(data=data, sources=[{"url": "https://primary.example.test/survey.pdf"}], model="synthetic-review-model", response_id="synthetic-response-" + role)

    def run_daily(self, enabled=True, attempt="1"):
        env = {"AI_RESEARCH_ENABLED": "true" if enabled else "false", "OPENAI_HARD_LIMIT_CONFIRMED": "10", "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": attempt}
        ledger = SimpleNamespace(remaining=lambda: 10_000_000)
        store = SimpleNamespace(read=lambda: ({}, "unused"), persist=lambda *_: True)
        def validate(*args):
            return apply_review(*args, now=fixtures.NOW)
        with patch.dict("os.environ", env), patch.object(runner, "HERE", self.config), patch.object(runner, "datetime", FixedClock), patch.object(runner, "GitHubLedgerStore", return_value=store), patch.object(runner, "BudgetLedger", return_value=ledger), patch.object(runner, "OpenAIEditorialClient", return_value=SimpleNamespace(generate=self.generate)), patch.object(runner, "collect", side_effect=self.fake_collect), patch.object(runner, "apply_review", side_effect=validate), patch.object(socket, "getaddrinfo", side_effect=AssertionError("Network forbidden in fixture tests")):
            runner.run("daily", self.repo, self.root / "accepted-brief-unused", self.output, self.work)

    def test_daily_full_chain_uses_independent_review_then_complete_outputs(self):
        before = (self.repo / "sentiment/data.json").read_bytes()
        self.run_daily()
        self.assertEqual([x["request_id"] for x in self.calls], ["12345.1:daily:discovery", "12345.1:daily:candidate", "12345.1:daily:review"])
        self.assertTrue(self.calls[0]["search"])
        self.assertFalse(self.calls[1]["search"])
        self.assertFalse(self.calls[2]["search"])
        self.assertEqual(self.calls[2]["kind"], "review")
        self.assertIn("existing_zonal_evidence", self.calls[1]["prompt"])
        self.assertIn("existing_state_evidence", self.calls[1]["prompt"])
        for name in ("data.json", "review.json"):
            self.assertTrue((self.output / "files/sentiment" / name).is_file())
        plan = json.loads((self.output / "plan.json").read_text())
        self.assertEqual(set(plan["files"]), {"sentiment/data.json", "sentiment/review.json"})
        self.assertEqual(before, (self.repo / "sentiment/data.json").read_bytes())
        summary = json.loads((self.output / "run-summary.json").read_text())
        self.assertFalse(summary["publication_changed"])
        self.assertIn("still required", summary["note"])
        review = json.loads((self.output / "files/sentiment/review.json").read_text())
        self.assertIn("Only supplied fixture evidence", review["scope"])
        self.assertIn("DAILY SENTIMENT EVIDENCE CHECK", self.calls[2]["prompt"])

    def test_drafting_receives_same_publication_window_as_reviewer(self):
        self.run_daily()
        for call in self.calls:
            self.assertIn("2026-09-10", call["prompt"])
        self.assertIn('eligible as NEW evidence', self.calls[1]['prompt'])

    def test_old_new_evidence_is_blocked_before_paid_review(self):
        self.packet['manifest']['sources'][0]['published_at'] = '2026-08-31'
        with self.assertRaisesRegex(ValueError, 'outside the trusted publication window'):
            self.run_daily()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1]['schema']['properties']['polls']['maxItems'], 0)
        self.assertFalse((self.output / 'files').exists())

    def test_older_background_can_only_support_reviewed_no_change(self):
        self.packet['manifest']['sources'][0]['published_at'] = '2026-08-31'
        self.packet['candidate']['polls'] = []
        self.packet['candidate']['security_context'] = []
        self.packet['candidate']['state_rating_proposals'] = []
        self.packet['candidate']['evidence_manifest_sha256'] = fixtures.sha(json.dumps(self.packet['manifest'], indent=2).encode())
        # The collector receipt is the authoritative bytes used in both bindings.
        (self.root / 'fixture/manifest.json').write_text(json.dumps(self.packet['manifest'], indent=2))
        before = (self.repo / 'sentiment/data.json').read_bytes()
        self.run_daily()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual((self.output / 'files/sentiment/data.json').read_bytes(), before)

    def test_publication_window_includes_cutoff_and_excludes_future(self):
        manifest = {'sources': [{'id': 'cutoff', 'published_at': '2026-09-10'},
                                {'id': 'old', 'published_at': '2026-09-09'},
                                {'id': 'future', 'published_at': '2026-10-26'}]}
        cutoff, ids = runner.daily_source_eligibility(manifest, fixtures.NOW.date())
        self.assertEqual(cutoff, '2026-09-10')
        self.assertEqual(ids, {'cutoff'})
        with self.assertRaisesRegex(ValueError, 'outside the trusted publication window'):
            runner.validate_daily_source_eligibility({'security_context': [{'source_id': 'old'}]}, ids)

    def test_explicit_retry_has_distinct_request_ids(self):
        self.run_daily(attempt="2")
        self.assertTrue(all(x["request_id"].startswith("12345.2:") for x in self.calls))

    def test_disabled_gate_prevents_every_model_call(self):
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            self.run_daily(False)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.output / "files").exists())

    def test_failed_collection_stops_before_candidate_and_reviewer(self):
        self.collect_failure = True
        with self.assertRaisesRegex(ValueError, "No source"):
            self.run_daily()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.output / "files").exists())
        summary = json.loads((self.output / 'run-summary.json').read_text())
        self.assertTrue(summary['bounded_recovery_attempted'])
        self.assertEqual(summary['status'], 'source_collection_withheld')

    def test_collection_gap_recovers_once_and_still_requires_review(self):
        policy_path = self.config / 'policy.json'
        policy = json.loads(policy_path.read_text())
        policy['publishers'][self.blocked_domain] = 'Blocked Publisher'
        policy_path.write_text(json.dumps(policy))
        self.initial_collect_failure = True
        self.run_daily()
        self.assertEqual([c['request_id'].split(':')[-1] for c in self.calls],
                         ['discovery', 'source-recovery', 'candidate', 'review'])
        recovery = self.calls[1]
        self.assertTrue(recovery['search'])
        self.assertEqual(recovery['kind'], 'daily')
        self.assertNotIn(self.blocked_domain, recovery['allowed_domains'])
        self.assertIn('2026-09-10', recovery['prompt'])
        self.assertTrue((self.output / 'plan.json').is_file())

    def test_recovery_budget_refusal_preserves_publication(self):
        self.initial_collect_failure = self.recovery_budget_failure = True
        before = (self.repo / 'sentiment/data.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'budget reservation refused'):
            self.run_daily()
        self.assertFalse((self.output / 'plan.json').exists())
        self.assertEqual(before, (self.repo / 'sentiment/data.json').read_bytes())

    def test_all_publishers_blocked_makes_no_recovery_call(self):
        self.initial_collect_failure = True
        self.blocked_domain = 'primary.example.test'
        with self.assertRaisesRegex(ValueError, 'No accessible approved publisher'):
            self.run_daily()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.output / 'files').exists())

    def test_changed_binding_is_not_silently_repaired(self):
        self.bad_candidate = True
        with self.assertRaisesRegex(ValueError, "trusted binding"):
            self.run_daily()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.output / "files").exists())

    def test_reviewer_disagreement_does_not_emit_public_files(self):
        self.disagree = True
        with self.assertRaisesRegex(ValueError, "withheld"):
            self.run_daily()
        self.assertEqual(len(self.calls), 3)
        self.assertFalse((self.output / "files").exists())
        self.assertFalse(json.loads((self.output / "run-summary.json").read_text())["publication_changed"])


if __name__ == "__main__":
    unittest.main()
