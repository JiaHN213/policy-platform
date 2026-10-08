from unittest.mock import patch

import pytest
from core.config_views import AIModelProfileSerializer
from core.models import AIModelProfile


@pytest.mark.django_db
@pytest.mark.parametrize("docker,url,valid", [
    (True, "http://localhost:11434/v1", False),
    (True, "http://127.0.0.1:11434", False),
    (True, "http://host.docker.internal:11434/v1", True),
    (False, "http://localhost:11434/v1", True),
])
def test_ollama_address_respects_deployment(docker, url, valid):
    profile = AIModelProfile(purpose="search", base_url=url, model="qwen3.5:4b")
    serializer = AIModelProfileSerializer(profile, data={"base_url": url}, partial=True)
    with patch("core.config_views.Path.exists", return_value=docker):
        assert serializer.is_valid() is valid
    if not valid:
        assert "host.docker.internal" in str(serializer.errors)
