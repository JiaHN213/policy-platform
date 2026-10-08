"""Export P0 design contracts. This does not register or enable runtime tools."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "packages/agent-contract/v1"
DRAFT = "https://json-schema.org/draft/2020-12/schema"


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def string(limit=200):
    return {"type": "string", "minLength": 1, "maxLength": limit}


def integer(maximum):
    return {"type": "integer", "minimum": 1, "maximum": maximum}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def export(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    version = integer(2147483647)
    policy = {"policy_id": string(36), "policy_version": version}
    profile = {"profile_id": string(36), "profile_revision": version}
    tools = []

    def tool(name, roles, permission, modes, side_effect, arguments, required=None):
        tools.append({"name": name, "roles": roles, "permission": permission,
                      "source_modes": modes, "side_effect": side_effect,
                      "status": "contract_only", "input_schema": {"$schema": DRAFT,
                      **obj(arguments, required)}})

    tool("read_confirmed_profile", ["profile", "retrieval", "matching"], "active_org_member",
         [], "none", profile | {"project_id": string(36)}, list(profile))
    tool("list_material_sections", ["profile"], "run_owner_and_material_grant", [], "none",
         {"material_id": string(36), "material_revision": version})
    tool("read_material_section", ["profile"], "run_owner_and_material_grant", [], "none",
         {"material_id": string(36), "material_revision": version, "section_id": string(),
          "max_chars": integer(12000)})
    tool("read_company_website", ["profile"], "run_owner_and_source_grant", ["website"],
         "external_read", {"url": {**string(2000), "format": "uri"}})
    tool("search_company_public_info", ["profile"], "run_owner_and_source_grant", ["search"],
         "external_read", {"name": string(), "city": string(80), "credit_code": string(18)}, ["name"])
    tool("search_formal_policies", ["retrieval"], "active_user_formal_visibility", [], "none", {
        "query": string(500), "view": enum("policies", "opportunities"),
        "filters": obj({"region": string(100), "business_domain": string(80),
                        "direction_tag": string(80), "category": string(80)}, []),
        "limit": integer(40), "cursor": string(500),
    }, ["query", "view", "filters", "limit"])
    tool("read_policy_evidence", ["retrieval", "matching", "wiki", "repair"],
         "formal_or_scoped_internal_policy", [], "none",
         policy | {"section_id": string(), "max_chars": integer(12000)})
    tool("read_opportunity_batch", ["retrieval", "matching"], "active_user_formal_visibility",
         [], "none", {"opportunity_id": string(36), "policy_version": version,
                      "opportunity_updated_at": {"type": "string", "format": "date-time"}})
    tool("compare_requirements", ["matching"], "active_org_member", [], "none",
         profile | {"requirements_artifact_id": string(36)})
    tool("inspect_relation_candidate", ["wiki"], "staff_and_relation_permission", [], "none",
         {"candidate_id": string(36),
          "candidate_updated_at": {"type": "string", "format": "date-time"}})
    tool("validate_relation_proposal", ["wiki"], "staff_and_relation_permission", [], "none",
         {"proposal_artifact_id": string(36)})
    tool("request_supported_reparse", ["repair"], "staff_and_reparse_permission", [],
         "idempotent_job", policy | {"attachment_id": string(36)}, list(policy))
    tool("save_result_draft", ["profile", "matching", "wiki", "repair"], "run_owner_or_internal_scope",
         [], "draft_only", {"artifact_id": string(36), "artifact_revision": version})
    export("tools.json", {"version": "agent-contract-v1", "runtime_enabled": False, "tools": tools})

    evidence = obj({
        "source_type": enum("policy", "attachment", "company_website", "uploaded_material", "pasted_text"),
        "source_id": string(36), "source_version": version, "segment_id": string(),
        "segmenter_version": string(80), "content_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "quote": string(3000), "start": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 1},
        "retrieved_at": {"type": "string", "format": "date-time"},
        "origin": enum("official", "company_statement", "user_statement"),
    })
    export("evidence.schema.json", {"$schema": DRAFT, "title": "EvidenceReferenceV1", **evidence})
    context = obj({
        "run_id": string(36), "actor_id": string(36),
        "organization_id": {"type": ["string", "null"], "maxLength": 36},
        "role": enum("profile", "retrieval", "matching", "wiki", "repair"),
        "permission_version": version, "lease_token": string(36),
        "source_mode": enum("website", "file", "text", "search", "internal"),
        "allowed_material_ids": {"type": "array", "items": string(36), "maxItems": 100},
        "config_version": string(80), "logical_call_id": string(),
    })
    export("context.schema.json", {"$schema": DRAFT, "title": "ServerInjectedContextV1", **context})
    response = obj({
        "status": enum("ok", "empty", "insufficient", "error"),
        "artifact_ids": {"type": "array", "items": string(36), "maxItems": 40},
        "evidence_refs": {"type": "array", "items": evidence, "maxItems": 60},
        "truncated": {"type": "boolean"},
        "next_cursor": {"type": ["string", "null"], "maxLength": 500},
        "warnings": {"type": "array", "items": string(500), "maxItems": 20},
        "error_code": {"type": ["string", "null"], "maxLength": 80},
        "usage": obj({"tool_calls": {"type": "integer", "minimum": 0},
                      "elapsed_ms": {"type": "number", "minimum": 0},
                      "input_tokens": {"type": ["integer", "null"], "minimum": 0},
                      "output_tokens": {"type": ["integer", "null"], "minimum": 0}}),
    })
    export("response.schema.json", {"$schema": DRAFT, "title": "ToolResponseEnvelopeV1", **response})
    budget = obj({"model_requests": integer(20), "tool_calls": integer(50),
                  "supplement_rounds": {"type": "integer", "minimum": 0, "maximum": 3},
                  "active_seconds": integer(1800), "max_input_tokens": integer(24576),
                  "max_output_tokens": integer(4096), "graph_steps": integer(40)})
    purpose = enum("enterprise", "search", "review", "wiki_relations", "wiki_synthesis")
    settings = obj({
        "schema_version": {"const": "1.0"}, "release_version": string(80),
        "enabled": {"type": "boolean"},
        "pilot_organization_ids": {"type": "array", "items": string(36), "uniqueItems": True},
        "shared_model_concurrency": integer(4), "await_input_days": integer(30),
        "workflows": obj({name: obj({"enabled": {"type": "boolean"},
            "model_purpose": purpose, "budget": budget, "fallback": enum(fallback)})
            for name, fallback in (("enterprise", "legacy_company_graph"),
                ("matching", "legacy_tag_matching"), ("wiki", "legacy_relation_pipeline"),
                ("repair", "legacy_review_pipeline"))}),
    })
    export("runtime.schema.json", {"$schema": DRAFT, "title": "AgentRuntimeConfigV1", **settings})
    export("runtime.defaults.json", {
        "schema_version": "1.0", "release_version": "agent-p0-1.0", "enabled": False,
        "pilot_organization_ids": [], "shared_model_concurrency": 1, "await_input_days": 7,
        "workflows": {name: {"enabled": False, "model_purpose": purpose_name,
            "fallback": fallback, "budget": {"model_requests": calls, "tool_calls": tools_count,
                "supplement_rounds": rounds, "active_seconds": seconds, "max_input_tokens": 20000,
                "max_output_tokens": 3500, "graph_steps": 30}}
            for name, purpose_name, fallback, calls, tools_count, rounds, seconds in (
                ("enterprise", "enterprise", "legacy_company_graph", 6, 12, 2, 600),
                ("matching", "enterprise", "legacy_tag_matching", 8, 20, 2, 900),
                ("wiki", "wiki_relations", "legacy_relation_pipeline", 4, 8, 1, 300),
                ("repair", "review", "legacy_review_pipeline", 3, 6, 1, 300),
            )},
    })
    export("errors.json", {"version": "1.0", "items": [
        {"code": code, "message": message, "action": action, "retryable": retry}
        for code, message, action, retry in (
            ("FORBIDDEN", "当前账号无权读取这份资料。", "检查企业成员权限", False),
            ("STALE_INPUT", "资料或政策已更新，本次结果需要重新生成。", "刷新后重新发起", False),
            ("NO_EVIDENCE", "没有找到支持该结论的原文。", "查看原文或补充资料", False),
            ("MODEL_UNAVAILABLE", "模型服务暂时不可用。", "稍后重试或联系管理员", True),
            ("BUDGET_EXHAUSTED", "本次处理已达到上限，已保存完成部分。", "查看结果后选择是否继续", False),
            ("SOURCE_UNAVAILABLE", "资料页面暂时无法读取。", "上传介绍或粘贴正文", True),
            ("FORMAT_UNSUPPORTED", "当前无法解析这种材料。", "提供可复制文字的文件", False),
            ("IDENTITY_AMBIGUOUS", "发现多家同名企业，尚未确定对应企业。", "选择正确企业", False),
            ("CANCELLED", "任务已停止，原有资料未被覆盖。", "需要时重新发起", False),
        )]})


if __name__ == "__main__":
    main()
