from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from accounts.models import Entitlement, Membership, Organization, User
from django.utils import timezone
from enterprises.models import EnterpriseProfile, EnterpriseProject, PolicyWatch, WatchRun
from policies.models import Policy
from quality.matching import create_study, metrics, observe_runtime, record_observation
from quality.models import MatchingCase, MatchingObservation, MatchingStudy
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    user = User.objects.create_user("matching-evaluator", is_staff=True)
    org = Organization.objects.create(name="合成水务企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(
        organization=org, confirmed_at=timezone.now(), data={"business_domains": ["urban_sewage"]}
    )
    for index in range(6):
        Policy.objects.create(
            title="污水运营管理办法" if index < 3 else "其他产业管理办法",
            body="支持城镇污水处理设施建设。" if index < 3 else "支持其他产业发展。",
            source_key=str(index),
            content_hash=str(index),
            source_url=f"https://example.gov.cn/{index}",
            publication_date=date(2026, 1, 1),
            status="published",
            source_grade="L1",
            validity_status="effective",
            business_domains=["urban_sewage"] if index < 3 else [],
            geographic_level="national",
        )
    client = APIClient()
    client.force_authenticate(user)
    return SimpleNamespace(user=user, profile=profile, client=client)


def sample(f):
    return create_study(f.user, f.profile, view="policies", limit=4)


def label(f, study, case, **overrides):
    values = {
        "verdict": "relevant",
        "quote": "支持城镇污水处理设施建设",
        "notes": "适用于本企业的污水处理业务",
        "evidence_supported": True,
        "label_version": case.label_version,
        **overrides,
    }
    return f.client.post(
        f"/api/v1/matching-studies/{study.pk}/label/{case.pk}", values, format="json"
    )


def test_sample_balanced_with_frozen_documents_without_model_calls(setup, monkeypatch):
    from enterprises import retrieval

    monkeypatch.setattr(
        retrieval, "index_candidates", lambda *args: pytest.fail("must not use index")
    )
    f = setup
    response = f.client.post(
        "/api/v1/matching-studies/sample",
        {"profile": str(f.profile.pk), "view": "policies", "limit": 4},
        format="json",
    )
    assert response.status_code == 201, response.data
    study = MatchingStudy.objects.get()
    assert study.cases.count() == 4
    assert study.cases.filter(predicted=True).count() == 2
    assert study.cases.filter(predicted=False).count() == 2
    assert study.selection["population"] == 6
    case = study.cases.filter(predicted=True).first()
    old = case.snapshot["body"]
    Policy.objects.filter(pk=case.policy_id).update(body="已修改正文", version=2)
    f.profile.data = {"business_domains": ["water_supply"]}
    f.profile.save()
    case.refresh_from_db()
    assert case.snapshot["body"] == old
    assert study.snapshot["profile"]["business_domains"] == ["urban_sewage"]
    assert label(f, study, case).status_code == 200


@pytest.mark.parametrize("kind", ["other_owner", "inactive_membership", "capability"])
def test_private_snapshots_and_labels_are_scoped(setup, kind):
    f = setup
    study = sample(f)
    case = study.cases.first()
    if kind == "other_owner":
        other = User.objects.create_user("colleague", is_staff=True)
        Membership.objects.create(user=other, organization=f.profile.organization, role="admin")
        f.client.force_authenticate(other)
    elif kind == "inactive_membership":
        Membership.objects.filter(user=f.user).update(active=False)
    else:
        Entitlement.objects.create(user=f.user, capability="policy_detail", allowed=False)
    assert f.client.get("/api/v1/matching-studies").data["count"] == 0
    assert f.client.get(f"/api/v1/matching-studies/{study.pk}").status_code == 404
    assert label(f, study, case).status_code == 404


def test_cross_enterprise_project_and_foreign_case_rejected(setup):
    f = setup
    other_org = Organization.objects.create(name="另一个企业")
    other_profile = EnterpriseProfile.objects.create(organization=other_org)
    project = EnterpriseProject.objects.create(profile=other_profile, name="他人项目")
    response = f.client.post(
        "/api/v1/matching-studies/sample",
        {"profile": str(f.profile.pk), "project": str(project.pk)},
        format="json",
    )
    assert response.status_code == 404
    one, two = sample(f), sample(f)
    assert label(f, one, two.cases.first()).status_code == 404


def test_quote_validation_and_optimistic_label_version(setup):
    f = setup
    study = sample(f)
    case = study.cases.filter(predicted=True).first()
    assert label(f, study, case, quote="不是原文的证据文字").status_code == 400
    assert label(f, study, case, quote="支持").status_code == 400
    assert label(f, study, case).status_code == 200
    assert label(f, study, case).status_code == 409
    case.refresh_from_db()
    assert (
        label(f, study, case, verdict="unsure", quote="", evidence_supported=None).status_code
        == 200
    )
    case.refresh_from_db()
    assert case.label_version == 2
    assert case.evidence_supported is None


def test_confusion_matrix_excludes_unknown_and_empty_denominators(setup):
    study = sample(setup)
    empty = metrics(study)
    assert empty["precision"] is None and empty["recall"] is None
    positive = list(study.cases.filter(predicted=True))
    negative = list(study.cases.filter(predicted=False))
    for case, verdict in [
        (positive[0], "relevant"),
        (positive[1], "irrelevant"),
        (negative[0], "relevant"),
        (negative[1], "irrelevant"),
    ]:
        case.verdict = verdict
        case.save()
    result = metrics(study)
    assert [result[key] for key in ["tp", "fp", "fn", "tn"]] == [1, 1, 1, 1]
    assert result["precision"] == 0.5 and result["recall"] == 0.5
    assert result["evidence_support"] is None
    MatchingCase.objects.filter(pk=positive[1].pk).update(verdict="unsure")
    result = metrics(study)
    assert result["precision"] == 1 and result["coverage"] == 0.75 and result["unsure"] == 1


def test_creation_limit_and_lost_membership_while_sampling(setup, monkeypatch):
    f = setup
    from quality import matching

    original = matching.match_policies

    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        Membership.objects.filter(user=f.user).update(active=False)
        return result

    monkeypatch.setattr(matching, "match_policies", revoked)
    response = f.client.post(
        "/api/v1/matching-studies/sample",
        {"profile": str(f.profile.pk), "view": "policies"},
        format="json",
    )
    assert response.status_code == 403
    assert not MatchingStudy.objects.exists()
    Membership.objects.filter(user=f.user).update(active=True)
    monkeypatch.setattr(matching, "match_policies", original)
    for _ in range(5):
        sample(f)
    response = f.client.post(
        "/api/v1/matching-studies/sample", {"profile": str(f.profile.pk)}, format="json"
    )
    assert response.status_code == 400
    assert MatchingStudy.objects.count() == 5


def test_observation_no_data_permissions_and_hourly_retention(setup):
    f = setup
    result = observe_runtime()
    assert result["status"] == "no_data" and result["p95_run_seconds"] is None
    assert f.client.get("/api/v1/admin/matching-observations").status_code == 403
    old = MatchingObservation.objects.create(hour=timezone.now() - timedelta(days=91), metrics={})
    assert record_observation() == record_observation()
    assert not MatchingObservation.objects.filter(pk=old.pk).exists()
    assert MatchingObservation.objects.count() == 1
    f.user.is_superuser = f.user.is_staff = True
    f.user.save()
    response = f.client.get("/api/v1/admin/matching-observations")
    assert response.status_code == 200
    assert "snapshot" not in str(response.data)


def test_observation_p95_and_expired_queue(setup):
    f = setup
    now = timezone.now()
    watch = PolicyWatch.objects.create(user=f.user, profile=f.profile, enabled=True)
    for duration in range(1, 21):
        run = WatchRun.objects.create(
            watch=watch, signature="test", cutoff=now, status="completed", finished_at=now
        )
        WatchRun.objects.filter(pk=run.pk).update(created_at=now - timedelta(seconds=duration))
    run = WatchRun.objects.create(
        watch=watch,
        signature="test",
        cutoff=now,
        status="running",
        lease_until=now - timedelta(minutes=1),
    )
    WatchRun.objects.filter(pk=run.pk).update(created_at=now - timedelta(hours=25))
    observed = observe_runtime()
    assert observed["p95_run_seconds"] == 19
    assert observed["expired_leases"] == 1 and observed["over_24h"] == 1
    assert observed["completed_7d"] == 20
