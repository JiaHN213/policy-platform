from datetime import date

import pytest
from policies.models import Policy, PolicySource
from policies.provenance import attach_policy_source, find_canonical_policy


def make_policy(**overrides):
    values = {
        "title": "南宁市供水管理办法",
        "document_number": "南府规〔2026〕1号",
        "issuer": "南宁市人民政府",
        "publication_date": date(2026, 1, 2),
        "source_url": "https://www.nanning.gov.cn/original.html",
        "source_key": "a" * 64,
        "content_hash": "b" * 64,
        "body": "供水政策正文",
    }
    values.update(overrides)
    return Policy.objects.create(**values)


@pytest.mark.django_db
def test_same_content_becomes_another_source_of_one_policy():
    policy = make_policy()
    attach_policy_source(
        policy,
        {
            "url": policy.source_url,
            "issuer": policy.issuer,
            "publication_date": policy.publication_date,
        },
        policy.content_hash,
        created_policy=True,
    )
    repost = {
        "title": policy.title,
        "url": "https://sthjt.gxzf.gov.cn/repost.html",
        "issuer": policy.issuer,
        "publication_date": policy.publication_date,
    }
    canonical, method, confidence, evidence = find_canonical_policy(
        repost, policy.content_hash
    )
    attach_policy_source(
        canonical,
        repost,
        policy.content_hash,
        match_method=method,
        confidence=confidence,
        evidence=evidence,
    )

    assert Policy.objects.count() == 1
    assert policy.sources.count() == 2
    source = policy.sources.get(url=repost["url"])
    assert source.role == PolicySource.Role.REPOST
    assert source.source_grade == Policy.SourceGrade.L2
    assert source.match_method == "content_hash"


@pytest.mark.django_db
def test_document_number_and_title_match_when_repost_body_has_wrapper_changes():
    policy = make_policy()
    record = {
        "title": "南宁市供水管理办法",
        "document_number": "南府规（2026）1号",
        "issuer": "南宁市人民政府",
        "publication_date": date(2026, 1, 2),
    }
    canonical, method, confidence, _ = find_canonical_policy(record, "c" * 64)
    assert canonical == policy
    assert method == "document_number_title"
    assert confidence > 0.98


@pytest.mark.django_db
def test_ambiguous_metadata_is_not_automatically_merged():
    make_policy(source_key="a" * 64)
    make_policy(source_key="c" * 64, source_url="https://example.gov.cn/second.html")
    record = {
        "title": "南宁市供水管理办法",
        "document_number": "南府规〔2026〕1号",
        "issuer": "南宁市人民政府",
        "publication_date": date(2026, 1, 2),
    }
    canonical, method, _, _ = find_canonical_policy(record, "d" * 64)
    assert canonical is None
    assert method == "new_policy"
