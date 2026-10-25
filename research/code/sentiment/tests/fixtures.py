"""Entirely synthetic evidence. Never publish these fixtures as real research."""
import datetime as dt
import json
from pathlib import Path
from apply_review import ZONES, json_bytes, sha

NOW = dt.datetime(2026, 10, 25, 12, tzinfo=dt.timezone.utc)
TEXT = """SYNTHETIC TEST DOCUMENT. This is not an actual survey or research source.
Published 20 October 2026 by Example Research.
Fieldwork took place during September 2026; exact days were not disclosed.
The Nigeria survey interviewed 2,470 adults aged 18 and over.
A stratified random telephone sample was weighted by sex within zone.
Telephone coverage and nonresponse limit representativeness; rural adults were underrepresented.
The question asked approval of presidential performance: 22 percent approved.
The question wording and mixed sample differ from the earlier survey; no like-for-like trend is justified.
The North East subsample contained 410 respondents; it is not the national sample.
In the North East, 39 percent approved presidential performance.
For Abia, a representative probability sample of 500 adults was selected by stratified random sampling.
Abia fieldwork ran from 2026-10-01 to 2026-10-10.
In Abia, 65 percent approved presidential performance.
The report recorded displacement in Nigeria during 2026-10-01 to 2026-10-19. This is security context, not public opinion polling.
"""


def fixture(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    baseline = {"updated": "2026-10-12", "updated_iso": "2026-10-12T12:00:00Z", "assessment_updated": "2026-09-27", "maintenance_checked": "2026-10-12", "national_evidence_updated": "2026-10-12", "national": {"president_approve": 30.2}, "classification": "Historical directional assessment", "sources": [{"name": "Historical source", "date": "2026-09-27"}], "method": "Legacy method remains unchanged.", "national_evidence": [], "zones": [{"id": z, "approve": 30, "disapprove": 50, "unsafe": 60} for z in ZONES], "states": [{"name": name, "zone": z, "president": "cold", "nsa": "cool", "note": "Historical assessment."} for z, names in ZONES.items() for name in names], "historical_extra": {"untouched": True}}
    extract = root / "extracts/source.txt"
    extract.parent.mkdir()
    extract.write_text(TEXT)
    source = {"id": "survey", "url": "https://primary.example.test/survey.pdf", "final_url": "https://primary.example.test/survey.pdf", "title": "Synthetic fixture survey", "publisher": "Example Research (synthetic)", "published_at": "2026-10-20", "fetched_at": "2026-10-25T10:00:00Z", "http_status": 200, "body_sha256": sha(TEXT.encode()), "text_sha256": sha(TEXT.encode()), "extract_path": "extracts/source.txt"}
    manifest = {"schema_version": 1, "sources": [source]}
    def cite(quote):
        return [{"source_id": "survey", "quote": quote, "locator": "Synthetic fixture paragraph"}]
    poll = {"id": "synthetic-survey-2026-10", "source_id": "survey", "geography": {"level": "national", "name": "Nigeria"}, "publication_date": "2026-10-20", "fieldwork": {"start": None, "end": None, "month": "2026-09", "note": "Exact days were not disclosed."}, "sample": {"size": 2470, "population": "Nigerian adults aged 18+", "selection": "Stratified random telephone sample", "weighting": "Sex within zone", "representative": False}, "results": [{"id": "president_approve", "label": "Presidential performance approval", "value": 22, "unit": "percent", "subject": "president", "measure": "approval", "citations": cite("The question asked approval of presidential performance: 22 percent approved.")}], "limitations": "Telephone coverage/nonresponse; rural underrepresentation.", "comparability": {"status": "not_comparable", "explanation": "Different wording and sample. No trend inferred."}, "citations": {"publication_date": cite("Published 20 October 2026 by Example Research."), "fieldwork": cite("Fieldwork took place during September 2026; exact days were not disclosed."), "sample": cite("The Nigeria survey interviewed 2,470 adults aged 18 and over."), "geography": cite("The Nigeria survey interviewed 2,470 adults aged 18 and over."), "method": cite("A stratified random telephone sample was weighted by sex within zone."), "limitations": cite("Telephone coverage and nonresponse limit representativeness; rural adults were underrepresented."), "comparability": cite("The question wording and mixed sample differ from the earlier survey; no like-for-like trend is justified.")}}
    candidate = {"schema_version": 1, "baseline_sha256": sha(json_bytes(baseline)), "evidence_manifest_sha256": sha(json_bytes(manifest)), "review_date": "2026-10-25", "polls": [poll], "security_context": [], "state_rating_proposals": []}
    attestation = {"schema_version": 1, "approved": True, "candidate_sha256": sha(json_bytes(candidate)), "evidence_manifest_sha256": candidate["evidence_manifest_sha256"], "reviewed_at": "2026-10-25T11:00:00Z", "model": "synthetic-independent-reviewer", "response_id": "synthetic-response-not-a-provider-call", "issues": []}
    policy = {"schema_version": 1, "primary_hosts": ["primary.example.test"], "polling_hosts": ["primary.example.test"], "state_rating_rules": []}
    packet = {"baseline": baseline, "candidate": candidate, "manifest": manifest, "attestation": attestation, "policy": policy}
    save(root, packet)
    return packet


def save(root, packet, rebind=True):
    if rebind:
        packet["candidate"]["baseline_sha256"] = sha(json_bytes(packet["baseline"]))
        packet["candidate"]["evidence_manifest_sha256"] = sha(json_bytes(packet["manifest"]))
        packet["attestation"]["candidate_sha256"] = sha(json_bytes(packet["candidate"]))
        packet["attestation"]["evidence_manifest_sha256"] = sha(json_bytes(packet["manifest"]))
    for name, value in packet.items():
        (Path(root) / (name + ".json")).write_bytes(json_bytes(value))


def state_packet(packet):
    poll = packet["candidate"]["polls"][0]
    poll["geography"] = {"level": "state", "name": "Abia"}
    quote = "For Abia, a representative probability sample of 500 adults was selected by stratified random sampling."
    for key in ("geography", "sample", "method"):
        poll["citations"][key][0]["quote"] = quote
    poll["sample"].update(size=500, representative=True, selection="Representative probability sample; stratified random sampling")
    poll["fieldwork"] = {"start": "2026-10-01", "end": "2026-10-10", "month": None, "note": "Exact dates disclosed."}
    poll["citations"]["fieldwork"][0]["quote"] = "Abia fieldwork ran from 2026-10-01 to 2026-10-10."
    poll["results"][0]["value"] = 65
    poll["results"][0]["citations"][0]["quote"] = "In Abia, 65 percent approved presidential performance."
    packet["candidate"]["state_rating_proposals"] = [{"id": "abia-president", "state": "Abia", "subject": "president", "tone": "warm", "poll_id": poll["id"], "result_id": "president_approve", "rationale": "Direct state approval result supports the separately approved classification rule.", "citations": poll["results"][0]["citations"]}]
    return packet
