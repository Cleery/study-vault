import pytest
from django.conf import settings
from django.test import Client


@pytest.mark.django_db
def test_django_settings_load_and_health_endpoint_returns_ok():
    assert settings.TIME_ZONE == "Asia/Shanghai"

    response = Client().get("/health/")

    assert response.status_code == 200
    assert response.content == b"ok"
