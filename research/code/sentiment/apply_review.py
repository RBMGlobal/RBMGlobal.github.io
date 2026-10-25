#!/usr/bin/env python3
"""Fail-closed sentiment evidence adapter. No network, model calls or publication.

The trusted runner owns collector receipts, allowlist policy and independent
reviewer attestation. Candidate content cannot create any of these inputs.
Exact passages establish traceability, not semantic truth; the independent
reviewer must assess authenticity, interpretation and methodological fitness.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

ZONES = {
    "NC": ["Benue", "FCT", "Kogi", "Kwara", "Nasarawa", "Niger", "Plateau"],
    "NE": ["Adamawa", "Bauchi", "Borno", "Gombe", "Taraba", "Yobe"],
    "NW": ["Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Sokoto", "Zamfara"],
    "SE": ["Abia", "Anambra", "Ebonyi", "Enugu", "Imo"],
    "SS": ["Akwa Ibom", "Bayelsa", "Cross River", "Delta", "Edo", "Rivers"],
    "SW": ["Ekiti", "Lagos", "Ogun", "Ondo", "Osun", "Oyo"],
}
ZONE_NAMES = {"NC": "North Central", "NE": "North East", "NW": "North West", "SE": "South East", "SS": "South South", "SW": "South West"}
STATES = {name: zone for zone, names in ZONES.items() for name in names}
TONES = {"cold", "cool", "mixed", "warm"}
HASH = re.compile(r"^[a-f0-9]{64}$")
IDENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")


class InvalidReview(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise InvalidReview(message)


def keys(value, required, optional=()):
    need(isinstance(value, dict), "Expected an object")
    need(set(required) <= set(value), "Missing fields: " + ", ".join(sorted(set(required) - set(value))))
    need(set(value) <= set(required) | set(optional), "Unexpected fields: " + ", ".join(sorted(set(value) - set(required) - set(optional))))


def text(value, limit=5000):
    need(isinstance(value, str) and 0 < len(value.strip()) <= limit, "Expected nonempty bounded text")
    need(not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value), "Control characters in text")
    return value


def identifier(value):
    need(isinstance(value, str) and IDENT.fullmatch(value), "Invalid record identifier")
    return value


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    need(isinstance(value, str) and HASH.fullmatch(value), "Invalid SHA-256")
    return value


def day(value):
    need(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "Expected exact ISO date")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise InvalidReview("Invalid calendar date") from error


def stamp(value):
    try:
        result = dt.datetime.fromisoformat(text(value, 50).replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidReview("Invalid timestamp") from error
    need(result.tzinfo is not None, "Timestamp must specify timezone")
    return result.astimezone(dt.timezone.utc)


def published_day(value):
    text(value, 50)
    return day(value) if len(value) == 10 else stamp(value).date()


def load_json(path):
    data = Path(path).read_bytes()
    need(len(data) <= 8_000_000, "Input exceeds size limit")
    def pairs(items):
        obj = {}
        for key, value in items:
            need(key not in obj, "Duplicate JSON key: " + key)
            obj[key] = value
        return obj
    try:
        return data, json.loads(data, object_pairs_hook=pairs,
                                parse_constant=lambda value: (_ for _ in ()).throw(InvalidReview("Non-finite JSON number")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise InvalidReview("Invalid JSON input") from error


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def validate_baseline(data):
    need(isinstance(data, dict), "Invalid baseline")
    states, zones = data.get("states"), data.get("zones")
    need(isinstance(states, list) and len(states) == 37, "Expected all 37 states/FCT")
    need(isinstance(zones, list) and len(zones) == 6, "Expected six zones")
    need(all(isinstance(s, dict) for s in states + zones), "Invalid baseline geography record")
    need({s.get("name") for s in states} == set(STATES), "Missing or duplicated state/FCT")
    need({z.get("id") for z in zones} == set(ZONES), "Missing or duplicated zone")
    for state in states:
        need(state.get("zone") == STATES[state["name"]], "State/zone mapping mismatch")
        need(state.get("president") in TONES and state.get("nsa") in TONES, "Invalid state tone")
        text(state.get("note"))
    for zone in zones:
        for key in ("approve", "disapprove", "unsafe"):
            number(zone.get(key), 0, 100)
    day(data.get("assessment_updated", data.get("updated")))
    for key in ("national_evidence", "zonal_evidence", "state_evidence"):
        need(isinstance(data.get(key, []), list), "Invalid existing evidence collection")


def number(value, low, high):
    need(type(value) in (int, float) and math.isfinite(value) and low <= value <= high, "Invalid bounded number")
    return value


def contains_number(passages, value):
    tokens = re.findall(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?![\w.])", passages)
    return any(float(token.replace(",", "")) == value for token in tokens)


def contains_date(passage, value):
    """Accept common explicit date spellings, never infer an exact day."""
    value = day(value)
    normalized = re.sub(r"\s+", " ", passage).lower()
    forms = {value.isoformat(), value.strftime("%d %B %Y").lstrip("0"), value.strftime("%d %b %Y").lstrip("0"), value.strftime("%B %d, %Y").replace(" 0", " "), value.strftime("%B %d %Y").replace(" 0", " ")}
    return any(form.lower() in normalized for form in forms)


def primary_url(value, hosts):
    parsed = urlsplit(text(value, 2000))
    need(parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443), "Source must be public HTTPS without credentials")
    need(parsed.hostname and any(parsed.hostname == host or parsed.hostname.endswith("." + host) for host in hosts), "Source hostname is not in trusted primary publisher policy")
    return value


def validate_sources(manifest, evidence_root, policy, now):
    # Collector may add collection diagnostics; candidates cannot set them.
    need(manifest.get("schema_version") == 1 and isinstance(manifest.get("sources"), list), "Invalid collector manifest")
    need(0 < len(manifest["sources"]) <= 100, "No collected sources, or too many sources")
    hosts = policy.get("primary_hosts")
    need(isinstance(hosts, list) and hosts and all(isinstance(h, str) and re.fullmatch(r"[a-z0-9.-]+", h) for h in hosts), "Missing trusted primary-host policy")
    sources = {}
    root = Path(evidence_root).resolve()
    for source in manifest["sources"]:
        required = {"id", "url", "final_url", "title", "publisher", "published_at", "fetched_at", "http_status", "body_sha256", "text_sha256", "extract_path"}
        need(isinstance(source, dict) and required <= source.keys(), "Incomplete collector source receipt")
        sid = identifier(source["id"])
        need(sid not in sources, "Duplicate source identifier")
        primary_url(source["url"], hosts)
        primary_url(source["final_url"], hosts)
        text(source["title"], 500); text(source["publisher"], 200)
        need(type(source["http_status"]) is int and source["http_status"] == 200, "Source fetch was not HTTP 200")
        fetched = stamp(source["fetched_at"])
        need(fetched <= now + dt.timedelta(minutes=5), "Source fetched in future")
        need(published_day(source["published_at"]) <= fetched.date(), "Publication postdates collection")
        digest(source["body_sha256"]); digest(source["text_sha256"])
        relative = Path(text(source["extract_path"], 500))
        need(not relative.is_absolute() and ".." not in relative.parts, "Extract path escapes evidence directory")
        path = root / relative
        need(path.is_file() and path.resolve().is_relative_to(root), "Extract missing or escapes evidence directory")
        content = path.read_bytes()
        need(0 < len(content) <= 5_000_000 and sha(content) == source["text_sha256"], "Collected extract digest mismatch")
        try:
            extract = content.decode("utf-8")
        except UnicodeError as error:
            raise InvalidReview("Extract must be UTF-8") from error
        sources[sid] = {**source, "_text": extract}
    return sources


def citations(values, sources, source_id=None):
    need(isinstance(values, list) and 1 <= len(values) <= 8, "Missing or excessive exact citations")
    quotes = []
    for value in values:
        keys(value, {"source_id", "quote", "locator"})
        sid = value["source_id"]
        need(sid in sources and (source_id is None or sid == source_id), "Citation references wrong/uncollected source")
        quote = text(value["quote"], 1600)
        need(len(quote.strip()) >= 12 and quote in sources[sid]["_text"], "Exact citation passage missing from collected extract")
        text(value["locator"], 200)
        quotes.append(quote)
    return "\n".join(quotes)


def public_citations(values, sources):
    return [{"source_id": c["source_id"], "url": sources[c["source_id"]]["url"], "locator": c["locator"]} for c in values]


def validate_poll(poll, sources, review_date, policy):
    keys(poll, {"id", "source_id", "geography", "publication_date", "fieldwork", "sample", "results", "limitations", "comparability", "citations"})
    identifier(poll["id"])
    sid = poll["source_id"]
    need(sid in sources, "Poll source not collected")
    polling_hosts = policy.get("polling_hosts")
    need(isinstance(polling_hosts, list) and polling_hosts and all(isinstance(host, str) and re.fullmatch(r"[a-z0-9.-]+", host) for host in polling_hosts), "Missing trusted originating polling-publisher policy")
    primary_url(sources[sid]["url"], polling_hosts)
    primary_url(sources[sid]["final_url"], polling_hosts)
    publication = day(poll["publication_date"])
    need(publication == published_day(sources[sid]["published_at"]) and publication <= review_date, "Poll publication date differs from collector receipt")
    geography = poll["geography"]
    keys(geography, {"level", "name"})
    need(geography["level"] in {"national", "zone", "state"}, "Invalid survey geography")
    names = {"national": {"Nigeria"}, "zone": set(ZONE_NAMES.values()), "state": set(STATES)}
    need(geography["name"] in names[geography["level"]], "Unknown/mismatched survey geography")
    cite_fields = {"publication_date", "fieldwork", "sample", "geography", "method", "limitations", "comparability"}
    keys(poll["citations"], cite_fields)
    passages = {key: citations(poll["citations"][key], sources, sid) for key in cite_fields}
    geo_text = re.sub(r"[-\s]+", " ", passages["geography"].lower())
    need(geography["name"].lower() in geo_text or (geography["name"] == "FCT" and "federal capital territory" in geo_text), "Geography not present in cited passage")
    fieldwork = poll["fieldwork"]
    keys(fieldwork, {"start", "end", "month", "note"})
    text(fieldwork["note"], 1000)
    need((fieldwork["start"] is None) == (fieldwork["end"] is None), "Partial fieldwork range")
    if fieldwork["start"] is not None:
        start, end = day(fieldwork["start"]), day(fieldwork["end"])
        need(start <= end <= publication, "Fieldwork dates are reversed or after publication")
        need(fieldwork["month"] is None, "Use either exact dates or month precision")
        need(contains_date(passages["fieldwork"], fieldwork["start"]) and contains_date(passages["fieldwork"], fieldwork["end"]), "Exact fieldwork dates not explicit in cited passage; retain month/unknown precision")
    elif fieldwork["month"] is not None:
        need(isinstance(fieldwork["month"], str) and re.fullmatch(r"\d{4}-\d{2}", fieldwork["month"]), "Invalid fieldwork month")
        need(day(fieldwork["month"] + "-01") <= publication, "Fieldwork month after publication")
        month = day(fieldwork["month"] + "-01")
        need(any(value.lower() in passages["fieldwork"].lower() for value in (fieldwork["month"], month.strftime("%B %Y"), month.strftime("%b %Y"))), "Fieldwork month absent from cited passage")
    sample = poll["sample"]
    keys(sample, {"size", "population", "selection", "weighting", "representative"})
    need(type(sample["size"]) is int and 30 <= sample["size"] <= 10_000_000, "Missing or invalid relevant-geography sample size")
    need(contains_number(passages["sample"], sample["size"]), "Sample size absent from cited passage")
    need(type(sample["representative"]) is bool, "Representative flag must be boolean")
    for key in ("population", "selection", "weighting"):
        text(sample[key], 2500)
    # A zonal/state sample must be explicitly tied to that geography in the
    # sample passage, not merely named elsewhere in a national report.
    if geography["level"] != "national":
        need(geography["name"].lower() in re.sub(r"[-\s]+", " ", passages["sample"].lower()) or (geography["name"] == "FCT" and "federal capital territory" in passages["sample"].lower()), "Subnational sample is not tied to matching geography")
    text(poll["limitations"], 3000)
    keys(poll["comparability"], {"status", "explanation"})
    need(poll["comparability"]["status"] in {"not_comparable", "separate_measure", "same_series"}, "Unknown comparability status")
    text(poll["comparability"]["explanation"], 2500)
    need(isinstance(poll["results"], list) and 1 <= len(poll["results"]) <= 20, "Missing or excessive survey results")
    ids = set()
    for result in poll["results"]:
        keys(result, {"id", "label", "value", "unit", "subject", "measure", "citations"})
        rid = identifier(result["id"])
        need(rid not in ids, "Duplicate result identifier"); ids.add(rid)
        text(result["label"], 200); text(result["measure"], 100)
        need(result["subject"] in {"president", "nsa", "public_security", "elections", "other"}, "Unknown survey subject")
        need(result["unit"] == "percent", "Only explicit survey percentages supported")
        number(result["value"], 0, 100)
        quote = citations(result["citations"], sources, sid)
        need(contains_number(quote, result["value"]), "Result number absent from cited passage")
        if geography["level"] != "national":
            need(geography["name"].lower() in re.sub(r"[-\s]+", " ", quote.lower()) or (geography["name"] == "FCT" and "federal capital territory" in quote.lower()), "Subnational result passage does not name matching geography")


def public_poll(poll, sources, reviewed):
    source = sources[poll["source_id"]]
    fieldwork, sample = poll["fieldwork"], poll["sample"]
    return {
        "id": poll["id"], "source_id": poll["source_id"], "source": source["title"], "publisher": source["publisher"], "url": source["url"],
        "publication_date": poll["publication_date"], "geography": poll["geography"],
        "fieldwork_month": fieldwork["month"], "fieldwork_exact_dates": {"from": fieldwork["start"], "to": fieldwork["end"]} if fieldwork["start"] else None,
        "fieldwork_note": fieldwork["note"], "sample": sample["size"], "population": sample["population"], "selection": sample["selection"],
        "weighting": sample["weighting"], "representative": sample["representative"], "limitations": poll["limitations"],
        "comparability": poll["comparability"]["explanation"], "comparability_status": poll["comparability"]["status"],
        "results": {r["id"]: r["value"] for r in poll["results"]},
        "result_details": [{k: v for k, v in r.items() if k != "citations"} | {"citations": public_citations(r["citations"], sources)} for r in poll["results"]],
        "reviewed": reviewed, "source_text_sha256": source["text_sha256"],
        "citations": {k: public_citations(v, sources) for k, v in poll["citations"].items()},
    }


def enforce_public_word_budget(polls, accepted_ids, context, applied, proposals, sources):
    """Cap newly published source-derived prose across every item per publisher.

    Exact private excerpts are removed from public output. Count prose metadata,
    result labels, security summaries and any applied state rationale together;
    identifiers, URLs, hashes, dates and fixed adapter explanations are excluded.
    """
    totals = {}
    def add(source_id, values):
        publisher = sources[source_id]["publisher"].strip().casefold()
        words = sum(len(re.findall(r"\b\w+(?:['’-]\w+)*\b", str(value))) for value in values)
        totals[publisher] = totals.get(publisher, 0) + words
        need(totals[publisher] <= 200, "New public source-derived prose exceeds 200 words for publisher: " + sources[source_id]["publisher"])
    for pid in accepted_ids:
        poll = polls[pid]
        add(poll["source_id"], [sources[poll["source_id"]]["title"], poll["fieldwork"]["note"], poll["sample"]["population"], poll["sample"]["selection"], poll["sample"]["weighting"], poll["limitations"], poll["comparability"]["explanation"], *(r["label"] for r in poll["results"])])
    for item in context:
        add(item["source_id"], [item["title"], item["summary"], item["geography"], item["event_window"]["note"]])
    applied_ids = {item["id"] for item in applied}
    for proposal in proposals:
        if proposal["id"] in applied_ids:
            add(polls[proposal["poll_id"]]["source_id"], [proposal["rationale"]])


def state_decision(proposal, polls, sources, attestation, policy, baseline):
    keys(proposal, {"id", "state", "subject", "tone", "poll_id", "result_id", "rationale", "citations"})
    identifier(proposal["id"]); text(proposal["rationale"], 2500)
    need(proposal["state"] in STATES and proposal["subject"] in {"president", "nsa"} and proposal["tone"] in TONES, "Invalid state proposal")
    need(proposal["poll_id"] in polls, "State proposal references absent poll")
    poll = polls[proposal["poll_id"]]
    citations(proposal["citations"], sources, poll["source_id"])
    result = next((r for r in poll["results"] if r["id"] == proposal["result_id"]), None)
    need(result is not None, "State proposal references absent result")
    reason = None
    if poll["geography"] != {"level": "state", "name": proposal["state"]}:
        reason = "A national or zonal result cannot determine a state rating."
    elif not poll["sample"]["representative"]:
        reason = "A directly representative state sample has not been established."
    elif not re.search(r"\b(probability|random)\b", " ".join(c["quote"] for c in poll["citations"]["method"]), re.I):
        reason = "Cited state sampling method does not establish probability or random selection."
    elif not poll["fieldwork"]["start"]:
        reason = "Exact state fieldwork dates are required for a rating change."
    elif day(poll["fieldwork"]["end"]) < day(baseline["assessment_updated"]):
        reason = "The fieldwork predates the existing assessment."
    elif result["subject"] != proposal["subject"] or result["measure"] != "approval":
        reason = "The survey does not directly measure approval of the matching subject."
    elif proposal["id"] not in attestation.get("approved_state_changes", []):
        reason = "The independent reviewer did not explicitly approve this state change."
    else:
        rules = policy.get("state_rating_rules", [])
        rule = next((r for r in rules if r.get("subject") == proposal["subject"] and r.get("measure") == result["measure"]), None)
        if rule is None:
            reason = "No owner-approved classification rule exists for this measure."
        else:
            keys(rule, {"subject", "measure", "bands", "approval_reference"})
            text(rule["approval_reference"], 1000)
            need(isinstance(rule["bands"], list) and len(rule["bands"]) == 4, "Invalid classification rule")
            previous = -1
            for band in rule["bands"]:
                keys(band, {"minimum", "tone"})
                number(band["minimum"], 0, 100)
                need(band["minimum"] > previous and band["tone"] in TONES, "Invalid classification bands")
                previous = band["minimum"]
            need(rule["bands"][0]["minimum"] == 0 and {b["tone"] for b in rule["bands"]} == TONES, "Incomplete classification bands")
            expected = next(b["tone"] for b in reversed(rule["bands"]) if result["value"] >= b["minimum"])
            if proposal["tone"] != expected:
                reason = "Proposed rating does not match the owner-approved classification rule."
    return reason


def apply_review(baseline_path, candidate_path, manifest_path, evidence_root, attestation_path, policy_path, output_dir, now=None):
    """Validate all inputs then atomically create new output/data.json + review.json.

    Existing output directories are refused. A rejected packet writes nothing.
    On no data change, data.json is exactly the original bytes, not reserialized.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    need(now.tzinfo is not None, "now must be timezone aware")
    baseline_bytes, baseline = load_json(baseline_path)
    candidate_bytes, candidate = load_json(candidate_path)
    manifest_bytes, manifest = load_json(manifest_path)
    _, attestation = load_json(attestation_path)
    _, policy = load_json(policy_path)
    validate_baseline(baseline)
    # The runner's combined trusted policy also owns budgets/discovery. Only
    # these fields affect sentiment; policy is not model-authored input.
    need(isinstance(policy, dict) and {"schema_version", "primary_hosts"} <= policy.keys(), "Missing sentiment policy fields")
    need(policy["schema_version"] == 1, "Unsupported policy version")
    keys(candidate, {"schema_version", "baseline_sha256", "evidence_manifest_sha256", "review_date", "polls", "security_context", "state_rating_proposals"})
    need(type(candidate["schema_version"]) is int and candidate["schema_version"] == 1, "Unsupported candidate version")
    need(digest(candidate["baseline_sha256"]) == sha(baseline_bytes), "Baseline digest mismatch; re-review latest published data")
    need(digest(candidate["evidence_manifest_sha256"]) == sha(manifest_bytes), "Evidence manifest digest mismatch")
    review_date = day(candidate["review_date"])
    need(review_date <= now.date() and review_date >= max(day(baseline["assessment_updated"]), day(baseline["updated"])), "Invalid review date")
    keys(attestation, {"schema_version", "approved", "candidate_sha256", "evidence_manifest_sha256", "reviewed_at", "model", "response_id", "issues"}, {"approved_state_changes"})
    need(attestation["schema_version"] == 1 and attestation["approved"] is True and attestation["issues"] == [], "Independent reviewer did not approve without unresolved issues")
    need(digest(attestation["candidate_sha256"]) == sha(candidate_bytes) and digest(attestation["evidence_manifest_sha256"]) == sha(manifest_bytes), "Reviewer attestation digest mismatch")
    reviewed_at = stamp(attestation["reviewed_at"])
    need(reviewed_at <= now + dt.timedelta(minutes=5) and reviewed_at.date() == review_date, "Reviewer date mismatch/future")
    need(reviewed_at >= now - dt.timedelta(hours=48), "Reviewer attestation is too old; re-review before publication")
    text(attestation["model"], 120); text(attestation["response_id"], 200)
    approved_states = attestation.get("approved_state_changes", [])
    need(isinstance(approved_states, list) and all(isinstance(x, str) for x in approved_states), "Invalid explicit state approvals")
    sources = validate_sources(manifest, evidence_root, policy, now)
    need(all(stamp(s["fetched_at"]) <= reviewed_at for s in sources.values()), "Reviewer attestation predates collection")
    for key, maximum in (("polls", 12), ("security_context", 12), ("state_rating_proposals", 37)):
        need(isinstance(candidate[key], list) and len(candidate[key]) <= maximum, "Invalid candidate collection: " + key)
    polls = {}
    for poll in candidate["polls"]:
        validate_poll(poll, sources, review_date, policy)
        need(poll["id"] not in polls, "Duplicate survey identifier")
        polls[poll["id"]] = poll
    data = copy.deepcopy(baseline)
    accepted, duplicate, withheld, applied, context = [], [], [], [], []
    existing = [item for key in ("national_evidence", "zonal_evidence", "state_evidence") for item in data.get(key, [])]
    for poll in polls.values():
        record = public_poll(poll, sources, candidate["review_date"])
        is_duplicate = any(item.get("id") == record["id"] or (item.get("url") == record["url"] and item.get("publication_date") == record["publication_date"] and item.get("geography", {"level": "national", "name": "Nigeria"}) == record["geography"]) for item in existing)
        if is_duplicate:
            duplicate.append(poll["id"])
            continue
        destination = {"national": "national_evidence", "zone": "zonal_evidence", "state": "state_evidence"}[poll["geography"]["level"]]
        data.setdefault(destination, []).append(record)
        existing.append(record)
        accepted.append(poll["id"])
    context_ids = set()
    for item in candidate["security_context"]:
        keys(item, {"id", "source_id", "title", "summary", "geography", "event_window", "citations"})
        identifier(item["id"])
        need(item["id"] not in context_ids, "Duplicate security context identifier"); context_ids.add(item["id"])
        need(item["source_id"] in sources, "Security source not collected")
        text(item["title"], 200); text(item["summary"], 4000); text(item["geography"], 200)
        keys(item["event_window"], {"from", "to", "note"})
        text(item["event_window"]["note"], 1000)
        start, end = item["event_window"]["from"], item["event_window"]["to"]
        need((start is None) == (end is None), "Partial security event window")
        context_quote = citations(item["citations"], sources, item["source_id"])
        if start is not None:
            need(day(start) <= day(end) <= review_date, "Invalid security event dates")
            need(contains_date(context_quote, start) and contains_date(context_quote, end), "Security event dates are not explicit in cited passage; use unknown window")
        for token in re.findall(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?(?![\w.])", item["title"] + " " + item["summary"]):
            need(contains_number(context_quote, float(token.replace(",", ""))), "Security-context number absent from cited passage")
        context.append({k: v for k, v in item.items() if k != "citations"} | {"citations": public_citations(item["citations"], sources), "publication_date": sources[item["source_id"]]["published_at"], "publisher": sources[item["source_id"]]["publisher"]})
    proposal_ids, changed_pairs = set(), set()
    for proposal in candidate["state_rating_proposals"]:
        need(proposal.get("id") not in proposal_ids, "Duplicate state proposal identifier"); proposal_ids.add(proposal.get("id"))
        reason = state_decision(proposal, polls, sources, attestation, policy, baseline)
        if reason:
            withheld.append({"id": proposal["id"], "state": proposal["state"], "subject": proposal["subject"], "reason": reason})
            continue
        pair = (proposal["state"], proposal["subject"])
        need(pair not in changed_pairs, "Conflicting state changes"); changed_pairs.add(pair)
        state = next(s for s in data["states"] if s["name"] == proposal["state"])
        if state[proposal["subject"]] == proposal["tone"]:
            continue
        old_tone = state[proposal["subject"]]
        state[proposal["subject"]] = proposal["tone"]
        state.setdefault("assessment_provenance", {})[proposal["subject"]] = {"assessed_at": candidate["review_date"], "poll_id": proposal["poll_id"], "result_id": proposal["result_id"], "rationale": proposal["rationale"], "previous_tone": old_tone}
        applied.append({"id": proposal["id"], "state": proposal["state"], "subject": proposal["subject"], "previous_tone": old_tone, "tone": proposal["tone"], "poll_id": proposal["poll_id"]})
    need(set(approved_states) <= proposal_ids, "Reviewer approved a nonexistent state proposal")
    enforce_public_word_budget(polls, accepted, context, applied, candidate["state_rating_proposals"], sources)
    if applied:
        data["assessment_updated"] = candidate["review_date"]
    if accepted:
        # This is an evidence-content date, explicitly not a state assessment.
        data["evidence_updated"] = candidate["review_date"]
        if any(polls[p]["geography"]["level"] == "national" for p in accepted):
            data["national_evidence_updated"] = candidate["review_date"]
    changed = data != baseline
    if changed:
        data["updated"] = candidate["review_date"]
        data["updated_iso"] = attestation["reviewed_at"]
        data["freshness_note"] = "State assessment dates are separate from evidence review dates. National and zonal survey evidence does not determine state ratings. The historical X sample remains unverified."
    validate_baseline(data)
    # Explicit immutability check, including unrelated future fields.
    mutable = {"states", "national_evidence", "zonal_evidence", "state_evidence", "evidence_updated", "national_evidence_updated", "updated", "updated_iso", "freshness_note", "assessment_updated"}
    need(all(data.get(k) == v for k, v in baseline.items() if k not in mutable), "Unexpected baseline mutation")
    output_bytes = json_bytes(data) if changed else baseline_bytes
    review = {
        "schema_version": 1, "reviewed_at": attestation["reviewed_at"], "review_date": candidate["review_date"],
        "status": "updated" if changed else "no_material_change", "data_changed": changed,
        "scope": "Review of the supplied, collected primary evidence; not an exhaustive survey of all publications.",
        "summary": "New verified evidence was accepted; source and assessment dates remain separate." if changed else "No material change accepted from the supplied evidence. Published assessments and data dates are unchanged.",
        "assessment_updated": data["assessment_updated"], "evidence_added": accepted, "duplicate_evidence": duplicate,
        "state_changes": applied, "withheld_state_changes": withheld, "security_context": context,
        "baseline_sha256": sha(baseline_bytes), "data_sha256": sha(output_bytes), "candidate_sha256": sha(candidate_bytes), "evidence_manifest_sha256": sha(manifest_bytes),
        "verification": {"method": "Independent AI review plus deterministic provenance and schema checks", "reviewed_at": attestation["reviewed_at"], "model": attestation["model"]},
        "sources": [{k: v for k, v in s.items() if k in {"id", "url", "final_url", "title", "publisher", "published_at", "fetched_at", "body_sha256", "text_sha256"}} for s in sources.values()],
        "caveat": "Exact citations and hashes establish retrieval and traceability. Independent review assesses interpretation; neither guarantees survey quality. Historical sources and X themes are not revalidated by this check.",
    }
    destination = Path(output_dir)
    need(not destination.exists(), "Output already exists; refusing to overwrite")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".sentiment-review-", dir=destination.parent))
    try:
        (temporary / "data.json").write_bytes(output_bytes)
        (temporary / "review.json").write_bytes(json_bytes(review))
        os.rename(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "manifest", "evidence-root", "attestation", "policy", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        result = apply_review(args.baseline, args.candidate, args.manifest, args.evidence_root, args.attestation, args.policy, args.output)
    except (InvalidReview, OSError) as error:
        parser.exit(1, "Sentiment review withheld: " + str(error) + "\n")
    print(json.dumps({"status": result["status"], "data_changed": result["data_changed"], "evidence_added": result["evidence_added"], "withheld_state_changes": result["withheld_state_changes"]}))


if __name__ == "__main__":
    main()
