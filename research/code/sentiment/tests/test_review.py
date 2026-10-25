import copy
import json
from pathlib import Path
import tempfile
import unittest
from apply_review import InvalidReview, apply_review, sha
from fixtures import NOW, fixture, save, state_packet


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packet = fixture(self.root)

    def run_packet(self, rebind=True):
        save(self.root, self.packet, rebind)
        return apply_review(*(self.root / (k + ".json") for k in ("baseline", "candidate", "manifest")), self.root, self.root / "attestation.json", self.root / "policy.json", self.root / "output", now=NOW)

    def invalid(self, pattern=None, rebind=True):
        with self.assertRaisesRegex(InvalidReview, pattern or ".+"):
            self.run_packet(rebind)
        self.assertFalse((self.root / "output").exists())
        self.assertFalse(list(self.root.glob(".sentiment-review-*")))

    def test_national_evidence_never_relabels_states_zones_or_baseline_metrics(self):
        report = self.run_packet()
        after = json.loads((self.root / "output/data.json").read_text())
        for key in ("states", "zones", "national", "sources", "method", "classification", "maintenance_checked", "assessment_updated", "historical_extra"):
            self.assertEqual(after[key], self.packet["baseline"][key])
        self.assertEqual(after["national_evidence"][0]["sample"], 2470)
        self.assertEqual(after["evidence_updated"], "2026-10-25")
        self.assertEqual(after["national_evidence"][0]["fieldwork_exact_dates"], None)
        self.assertTrue(report["data_changed"])
        self.assertEqual(report["data_sha256"], sha((self.root / "output/data.json").read_bytes()))
        self.assertEqual(set(p.name for p in (self.root / "output").iterdir()), {"data.json", "review.json"})
        self.assertNotIn("exact days were not disclosed", (self.root / "output/review.json").read_text())

    def test_no_change_preserves_exact_bytes_and_all_dates(self):
        self.packet["candidate"]["polls"] = []
        report = self.run_packet()
        self.assertEqual((self.root / "baseline.json").read_bytes(), (self.root / "output/data.json").read_bytes())
        self.assertEqual(report["status"], "no_material_change")
        self.assertIn("supplied", report["scope"])

    def test_missing_state_and_zone_rejected(self):
        self.packet["baseline"]["states"].pop()
        self.invalid("37")

    def test_duplicate_state_rejected(self):
        self.packet["baseline"]["states"][-1] = self.packet["baseline"]["states"][0]
        self.invalid("duplicated state")

    def test_wrong_state_zone_rejected(self):
        self.packet["baseline"]["states"][0]["zone"] = "SE"
        self.invalid("mapping")

    def test_missing_zone_rejected(self):
        self.packet["baseline"]["zones"].pop()
        self.invalid("six zones")

    def test_stale_baseline_hash_rejected(self):
        self.packet["candidate"]["baseline_sha256"] = "a" * 64
        self.invalid("Baseline digest", rebind=False)

    def test_manifest_tampering_rejected(self):
        self.packet["manifest"]["sources"][0]["title"] += " tampered"
        self.invalid("manifest digest", rebind=False)

    def test_candidate_tampering_after_reviewer_rejected(self):
        self.packet["candidate"]["polls"][0]["results"][0]["label"] = "Unreviewed edit"
        self.invalid("attestation digest", rebind=False)

    def test_review_disagreement_rejected(self):
        self.packet["attestation"]["approved"] = False
        self.invalid("did not approve")

    def test_review_unresolved_issues_rejected(self):
        self.packet["attestation"]["issues"] = ["Unverified fieldwork"]
        self.invalid("did not approve")

    def test_candidate_cannot_self_attest(self):
        self.packet["candidate"]["attestation"] = self.packet["attestation"]
        self.invalid("Unexpected fields")

    def test_uncollected_source_rejected(self):
        self.packet["candidate"]["polls"][0]["source_id"] = "invented"
        self.invalid("not collected")

    def test_failed_fetch_rejected(self):
        self.packet["manifest"]["sources"][0]["http_status"] = 403
        self.invalid("HTTP 200")

    def test_source_domain_requires_trusted_policy(self):
        self.packet["manifest"]["sources"][0]["url"] = "https://unapproved.example/source"
        self.invalid("primary publisher")

    def test_redirect_to_unapproved_host_rejected(self):
        self.packet["manifest"]["sources"][0]["final_url"] = "https://127.0.0.1/source"
        self.invalid("primary publisher")

    def test_secondary_reporting_cannot_supply_polling_numbers(self):
        self.packet["policy"]["polling_hosts"] = ["original-pollster.example"]
        self.invalid("primary publisher")

    def test_combined_policy_accepts_unrelated_runner_budget_fields(self):
        self.packet["policy"].update(monthly_limit_usd=10, max_sources=10, publishers={"primary.example.test": "Example Research"})
        self.assertTrue(self.run_packet()["data_changed"])

    def test_extract_hash_mismatch_rejected(self):
        (self.root / "extracts/source.txt").write_text("Tampered extracted source")
        self.invalid("extract digest")

    def test_extract_path_escape_rejected(self):
        self.packet["manifest"]["sources"][0]["extract_path"] = "../source.txt"
        self.invalid("escapes")

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            external = Path(other) / "source.txt"
            external.write_text("External content")
            (self.root / "extracts/source.txt").unlink()
            (self.root / "extracts/source.txt").symlink_to(external)
            self.invalid("escapes")

    def test_fabricated_excerpt_rejected(self):
        self.packet["candidate"]["polls"][0]["results"][0]["citations"][0]["quote"] = "This exact quote does not exist in the source."
        self.invalid("Exact citation")

    def test_number_absent_from_excerpt_rejected(self):
        self.packet["candidate"]["polls"][0]["results"][0]["value"] = 73
        self.invalid("Result number")

    def test_percentage_over_100_rejected(self):
        self.packet["candidate"]["polls"][0]["results"][0]["value"] = 122
        self.invalid("bounded number")

    def test_unknown_sample_size_rejected(self):
        self.packet["candidate"]["polls"][0]["sample"]["size"] = None
        self.invalid("sample size")

    def test_sample_size_not_in_source_rejected(self):
        self.packet["candidate"]["polls"][0]["sample"]["size"] = 999
        self.invalid("Sample size absent")

    def test_publication_date_mismatch_rejected(self):
        self.packet["candidate"]["polls"][0]["publication_date"] = "2026-10-21"
        self.invalid("publication date")

    def test_unknown_fieldwork_days_remain_null(self):
        poll = self.packet["candidate"]["polls"][0]
        poll["fieldwork"].update(start="2026-09-01", end="2026-09-30", month=None)
        self.invalid("Exact fieldwork dates")

    def test_zonal_result_cannot_reuse_national_sample(self):
        poll = self.packet["candidate"]["polls"][0]
        poll["geography"] = {"level": "zone", "name": "North East"}
        poll["citations"]["geography"][0]["quote"] = "The North East subsample contained 410 respondents; it is not the national sample."
        self.invalid("Subnational sample")

    def test_zonal_subsample_appends_without_changing_zone(self):
        poll = self.packet["candidate"]["polls"][0]
        poll["geography"] = {"level": "zone", "name": "North East"}
        poll["sample"]["size"] = 410
        for field in ("sample", "geography"):
            poll["citations"][field][0]["quote"] = "The North East subsample contained 410 respondents; it is not the national sample."
        poll["results"][0]["value"] = 39
        poll["results"][0]["citations"][0]["quote"] = "In the North East, 39 percent approved presidential performance."
        self.run_packet()
        after = json.loads((self.root / "output/data.json").read_text())
        self.assertEqual(after["zonal_evidence"][0]["sample"], 410)
        self.assertEqual(after["zones"], self.packet["baseline"]["zones"])

    def test_state_proposal_from_national_poll_withheld(self):
        proposal = state_packet(copy.deepcopy(self.packet))["candidate"]["state_rating_proposals"][0]
        proposal["citations"] = self.packet["candidate"]["polls"][0]["results"][0]["citations"]
        self.packet["candidate"]["state_rating_proposals"] = [proposal]
        report = self.run_packet()
        self.assertIn("national", report["withheld_state_changes"][0]["reason"])
        self.assertEqual(report["assessment_updated"], "2026-09-27")

    def test_state_change_requires_explicit_independent_approval(self):
        state_packet(self.packet)
        report = self.run_packet()
        self.assertIn("explicitly approve", report["withheld_state_changes"][0]["reason"])
        self.assertEqual(report["state_changes"], [])

    def test_state_change_requires_owner_defined_rule(self):
        state_packet(self.packet)
        self.packet["attestation"]["approved_state_changes"] = ["abia-president"]
        report = self.run_packet()
        self.assertIn("No owner-approved", report["withheld_state_changes"][0]["reason"])

    def test_tightly_gated_state_change_preserves_other_36_states(self):
        state_packet(self.packet)
        self.packet["attestation"]["approved_state_changes"] = ["abia-president"]
        # Synthetic test rule only, NOT a recommended/default real classification.
        self.packet["policy"]["state_rating_rules"] = [{"subject": "president", "measure": "approval", "approval_reference": "Synthetic rule for test only", "bands": [{"minimum": 0, "tone": "cold"}, {"minimum": 30, "tone": "cool"}, {"minimum": 45, "tone": "mixed"}, {"minimum": 60, "tone": "warm"}]}]
        report = self.run_packet()
        after = json.loads((self.root / "output/data.json").read_text())
        self.assertEqual(len(report["state_changes"]), 1)
        self.assertEqual(after["assessment_updated"], "2026-10-25")
        for a, b in zip(after["states"], self.packet["baseline"]["states"]):
            if a["name"] != "Abia":
                self.assertEqual(a, b)
            else:
                self.assertEqual(a["president"], "warm")
                self.assertEqual(a["nsa"], b["nsa"])

    def test_duplicate_poll_is_no_change_and_keeps_dates(self):
        self.run_packet()
        self.packet["baseline"] = json.loads((self.root / "output/data.json").read_text())
        import shutil
        shutil.rmtree(self.root / "output")
        report = self.run_packet()
        self.assertFalse(report["data_changed"])
        self.assertEqual(report["duplicate_evidence"], [self.packet["candidate"]["polls"][0]["id"]])

    def test_security_context_does_not_become_approval_poll_or_update_dates(self):
        self.packet["candidate"]["polls"] = []
        self.packet["candidate"]["security_context"] = [{"id": "security", "source_id": "survey", "title": "Attributed security context", "summary": "Example Research reports displacement; this is not an opinion measure.", "geography": "Nigeria", "event_window": {"from": "2026-10-01", "to": "2026-10-19", "note": "Source reporting window"}, "citations": [{"source_id": "survey", "quote": "The report recorded displacement in Nigeria during 2026-10-01 to 2026-10-19.", "locator": "Test paragraph"}]}]
        report = self.run_packet()
        self.assertEqual(report["security_context"][0]["publication_date"], "2026-10-20")
        self.assertFalse(report["data_changed"])

    def test_empty_collection_cannot_report_success(self):
        self.packet["manifest"]["sources"] = []
        self.invalid("No collected sources")

    def test_new_public_prose_is_capped_per_publisher(self):
        self.packet["candidate"]["polls"][0]["limitations"] = " ".join(["limitation"] * 201)
        self.invalid("200 words")

    def test_poll_and_security_prose_share_publisher_budget(self):
        self.packet["candidate"]["security_context"] = [{"id": "context", "source_id": "survey", "title": "Context", "summary": " ".join(["reported"] * 190), "geography": "Nigeria", "event_window": {"from": None, "to": None, "note": "Unknown exact event days"}, "citations": [{"source_id": "survey", "quote": "The report recorded displacement in Nigeria during 2026-10-01 to 2026-10-19.", "locator": "Synthetic paragraph"}]}]
        self.invalid("200 words")

    def test_security_context_number_requires_exact_support(self):
        self.packet["candidate"]["security_context"] = [{"id": "context", "source_id": "survey", "title": "Context", "summary": "The report recorded 5000 displaced people.", "geography": "Nigeria", "event_window": {"from": None, "to": None, "note": "Unknown exact event days"}, "citations": [{"source_id": "survey", "quote": "The report recorded displacement in Nigeria during 2026-10-01 to 2026-10-19.", "locator": "Synthetic paragraph"}]}]
        self.invalid("number absent")

    def test_output_refuses_overwrite(self):
        self.run_packet()
        original = (self.root / "output/data.json").read_bytes()
        with self.assertRaisesRegex(InvalidReview, "already exists"):
            self.run_packet()
        self.assertEqual(original, (self.root / "output/data.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
