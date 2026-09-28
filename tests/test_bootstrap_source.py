import pytest
from django.core.management import call_command
from ingestion.models import Source
from ingestion.nanning import URL as NANNING_LIBRARY_URL


@pytest.mark.django_db
def test_fresh_bootstrap_registers_only_nanning_and_preserves_configuration():
    call_command("bootstrap")

    source = Source.objects.get()
    assert source.url == NANNING_LIBRARY_URL
    assert source.name == "南宁市政策文件库"
    assert source.adapter == "nanning_v1"
    assert source.enabled is True
    assert source.interval_minutes == 1440
    assert source.verification_status == "verified"

    source.enabled = False
    source.save(update_fields=["enabled"])
    call_command("bootstrap")

    assert Source.objects.count() == 1
    source.refresh_from_db()
    assert source.enabled is False
