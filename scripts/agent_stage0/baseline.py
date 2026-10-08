"""Reproducible P0 baseline: synthetic fixtures, isolated memory DB, no network/LLM.

This records current behavior and known gaps separately. It is not an Agent evaluation.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))
# Force the isolated test settings even if the shell has a production configuration.
os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
os.environ.pop("TEST_DATABASE_URL", None)
os.environ["DATABASE_URL"] = "sqlite://:memory:"


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def validate_contracts():
    from jsonschema import Draft202012Validator, FormatChecker

    folder = "packages/agent-contract/v1/"
    for path in (ROOT / folder).glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
    schema = load(folder + "runtime.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    defaults = load(folder + "runtime.defaults.json")
    validator.validate(defaults)
    assert not defaults["enabled"] and not any(v["enabled"] for v in defaults["workflows"].values())
    invalid = json.loads(json.dumps(defaults))
    invalid["workflows"]["enterprise"]["budget"]["model_requests"] = -1
    assert not validator.is_valid(invalid)
    tools = load(folder + "tools.json")["tools"]
    assert len({t["name"] for t in tools}) == len(tools)
    for tool in tools:
        Draft202012Validator.check_schema(tool["input_schema"])
        assert not {"user_id", "organization_id", "role", "api_key"} & tool["input_schema"]["properties"].keys()
        assert tool["input_schema"]["additionalProperties"] is False
    schema = load(folder + "evidence.schema.json")
    Draft202012Validator.check_schema(schema)
    proof = {"source_type": "policy", "source_id": "synthetic-policy", "source_version": 1,
             "segment_id": "body-0", "segmenter_version": "v1", "content_sha256": "a" * 64,
             "quote": "支持污水处理项目", "start": 0, "end": 8,
             "retrieved_at": "2026-09-29T00:00:00+08:00", "origin": "official"}
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(proof)
    assert not validator.is_valid(proof | {"quote": ""})
    assert not validator.is_valid(proof | {"source_version": 0})
    return {"tools": len(tools), "configuration": "valid_and_disabled", "evidence": "valid"}


def material_case(data):
    from enterprises.materials import MaterialError, upload_text
    from pypdf import PdfWriter

    content, fmt = data["text"], data["format"]
    if fmt in {"txt", "md"}:
        raw = content.encode("utf-8")
    elif fmt == "pdf":
        writer, stream = PdfWriter(), BytesIO()
        writer.add_blank_page(width=100, height=100)
        writer.write(stream)
        raw = stream.getvalue()
    else:
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            if fmt == "docx":
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + escape(content) + '</w:t></w:r></w:p></w:body></w:document>')
            else:
                archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>' + escape(content) + '</a:t></a:r></a:p></p:sld>')
        raw = stream.getvalue()
    try:
        output = upload_text(raw, "synthetic." + fmt)
        return "contains" if content and content in output else "missing"
    except MaterialError:
        return "unreadable"


def draft_case(data):
    from enterprises.research import CompanyDraft, validate_draft

    source = {"id": 1, "url": "https://example.com/about", "title": "合成企业简介",
              "text": data["text"], "retrieved_at": "2026-09-29", "material": "合成材料",
              "source_type": "测试夹具，不是真实来源"}
    fields = [] if data.get("empty_fields") else [{"field": data["field"], "value": data["value"],
              "evidence": [{"source_id": 1, "quote": data["quote"]}]}]
    draft = CompanyDraft.model_validate({"candidates": [{"name": data["name"], "fields": fields,
            "identity_evidence": {"source_id": 1, "quote": data.get("identity_quote", data["name"])}}]})
    result = validate_draft(draft, [source], {"name": data["name"]})
    return result["candidates"][0]["data"] if result["candidates"] else None


def match_case(data, as_of):
    from accounts.models import Membership, Organization, User
    from enterprises.matching import match_policies
    from enterprises.models import EnterpriseProfile, EnterpriseProject
    from policies.models import Opportunity, OpportunityBatch, Policy

    user = User.objects.create(username="synthetic-agent-baseline")
    org = Organization.objects.create(name="合成评测企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org, data=data["profile"])
    project = EnterpriseProject.objects.create(profile=profile, name="合成项目", data=data["project"]) if "project" in data else None
    values = {"title": "合成智慧水务通知", "issuer": "合成部门", "publication_date": date(2026, 9, 1),
              "body": "支持污水处理设施进行智慧水务改造。", "summary": "合成材料",
              "source_url": "https://example.com/policy", "source_key": "synthetic",
              "content_hash": "synthetic", "status": "published", "source_grade": "L1",
              "validity_status": "effective", "geographic_level": "national", "is_demo": False,
              "business_domains": ["urban_sewage"], "direction_tags": ["smart_water"]}
    policy = Policy.objects.create(**(values | data.get("policy", {})))
    if data.get("view") == "opportunities":
        evidence = {"evidence_policy": policy, "evidence_version": data.get("opportunity_version", 1),
                    "evidence_quote": "支持污水处理设施", "verification_status": "verified"}
        opp = Opportunity.objects.create(policy=policy, title="合成机会", category="fiscal", status="open", **evidence)
        if data.get("deadline"):
            OpportunityBatch.objects.create(opportunity=opp, name="合成批次", status="open",
                deadline_at=datetime.fromisoformat(data["deadline"]), **evidence)
    with patch("enterprises.matching.timezone.now", return_value=as_of):
        result = match_policies(user, profile, project, view=data.get("view", "policies"))
    return result["items"][0]["level"] if result["items"] else "excluded"


def relation_case(data):
    from knowledge.relations import RelationProposal, proposal_diagnostics

    old = SimpleNamespace(pk="A", title="示例污水处理办法", document_number="示例〔2025〕1号",
                          publication_date=date(2025, 1, 1), document_type="policy", body="旧办法正文。")
    new = SimpleNamespace(pk="B", title="示例新通知", document_number="示例〔2026〕2号",
                          publication_date=date(2026, 1, 1), document_type="policy",
                          body=data.get("body", data["quote"]))
    source, target = (old, new) if data.get("reverse") else (new, old)
    proposal = RelationProposal(from_policy_id=source.pk, to_policy_id=target.pk,
        relation=data["relation"], evidence_policy_id=new.pk, evidence_quote=data["quote"],
        confidence=.9, reason="合成评测提案")
    errors, _ = proposal_diagnostics(proposal, {"A": old, "B": new}, "B", ["A"])
    return "rejected" if errors else "accepted"


def execute(case, as_of):
    kind, data = case["kind"], case["input"]
    if kind == "material":
        return material_case(data)
    if kind == "draft":
        return draft_case(data)
    if kind == "match":
        return match_case(data, as_of)
    if kind == "relation":
        return relation_case(data)
    if kind == "website":
        from enterprises.materials import MaterialError, normalize_website
        try:
            return normalize_website(data["url"])
        except MaterialError:
            return "rejected"
    raise ValueError("Unknown executable case kind")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "holdout", "all"], default="development")
    parser.add_argument("--output", default="output/agent-stage0/baseline.json")
    args = parser.parse_args()
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connection, transaction

    assert connection.vendor == "sqlite" and connection.settings_dict["NAME"] == ":memory:", "Refusing non-memory DB"
    report = {"captured_at": datetime.now(timezone.utc).isoformat(), "mode": "offline_current_code",
              "split": args.split, "llm_calls": 0, "network_calls": 0,
              "configuration": "repository_defaults_in_empty_test_database",
              "versions": {name: version(name) for name in ("django", "pydantic", "langgraph", "jsonschema")},
              "contracts": validate_contracts(), "results": []}
    corpus = load("tests/fixtures/agent_stage0/cases.json")
    cases = corpus["cases"]
    assert len(cases) == 40 and len({c["id"] for c in cases}) == 40
    assert {c["split"] for c in cases} == {"development", "holdout"}
    as_of = datetime.fromisoformat(corpus["as_of"])
    # Do not allow migrations or cases to dispatch real network work.
    with patch("socket.socket.connect", side_effect=RuntimeError("P0 network disabled")):
        call_command("migrate", verbosity=0, interactive=False)
        for case in cases:
            if args.split != "all" and args.split != case["split"]:
                continue
            row = {"id": case["id"], "title": case["title"], "kind": case["kind"], "split": case["split"]}
            if case["kind"] == "acceptance":
                row.update(status="not_implemented", target=case["target"], phase=case["phase"])
            else:
                start = time.perf_counter()
                try:
                    with transaction.atomic():
                        actual = execute(case, as_of)
                        transaction.set_rollback(True)
                    row.update(actual=actual, baseline_matches=actual == case["expected_current"],
                        target_met=actual == case["target"],
                        status="recorded" if actual == case["expected_current"] else "baseline_drift")
                    if case.get("gap"):
                        row["gap"] = case["gap"]
                except Exception as exc:
                    row.update(status="error", error_type=type(exc).__name__, error=str(exc)[:300])
                row["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
            report["results"].append(row)
    report["counts"] = dict(Counter(r["status"] for r in report["results"]))
    report["target_gaps"] = [r["id"] for r in report["results"] if r.get("target_met") is False]
    report["source_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (
        "apps/api/enterprises/research.py", "apps/api/enterprises/materials.py",
        "apps/api/enterprises/matching.py", "apps/api/knowledge/relations.py",
        "apps/api/policies/catalog.py", "configs/dictionaries/business_scope.json",
        "configs/dictionaries/wiki_relations.json", "scripts/agent_stage0/baseline.py",
        "tests/fixtures/agent_stage0/cases.json",
    )}
    path = ROOT / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": report["counts"], "target_gaps": report["target_gaps"],
                      "output": str(path)}, ensure_ascii=False))
    return int(any(r["status"] in {"error", "baseline_drift"} for r in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
