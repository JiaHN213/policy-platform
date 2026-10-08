import pytest
from django.core.cache import caches


@pytest.fixture(autouse=True)
def isolate_search_cache():
    # Database transactions do not roll back Redis/LocMem entries between tests.
    caches["search"].clear()
    yield
    caches["search"].clear()
