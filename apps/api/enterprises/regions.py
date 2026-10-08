"""Region preferences help ordering; unknown scope must not exclude a policy."""
import re

from policies.enrichment import grounded_quote


def region_preference(policy, regions, opportunities):
    if not regions:
        return {"matched": False, "label": "未限定关注地区"}
    for opportunity in opportunities:
        for evidence in opportunity.evidence_details:
            if not isinstance(evidence, dict) or evidence.get("field") != "regions":
                continue
            quote = grounded_quote(evidence.get("quote", ""), policy.body)
            if not quote or opportunity.evidence_version != policy.version or opportunity.evidence_policy_id != policy.pk:
                continue
            if re.search(r"(?:适用|面向|支持|申报).{0,8}全国", quote):
                return {"matched": True, "label": "原文表明面向全国，具体条件仍需核对"}
            for region in regions:
                if region and region in quote and any(region in str(r) or str(r) in region for r in opportunity.regions):
                    return {"matched": True, "label": f"适用地区证据涉及关注地：{region}"}
    return {"matched": False, "label": "关注地区：" + "、".join(regions) + "；适用范围尚需核对，已保留可能相关的全国及跨地区政策"}
