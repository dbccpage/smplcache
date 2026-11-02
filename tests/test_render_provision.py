import httpx
import pytest
import json

from scripts import deploy_and_verify_render as deploy


def test_create_service_uses_explicit_environment_and_python(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/services" and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path == "/v1/services" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "service": {
                        "id": "srv-test",
                        "name": deploy.SERVICE_NAME,
                        "serviceDetails": {"url": "https://actual.onrender.com"},
                    }
                },
            )
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"name": deploy.CUSTOM_DOMAIN})

    original_client = httpx.Client
    monkeypatch.setattr(
        deploy.httpx,
        "Client",
        lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw),
    )
    result = deploy.provision_or_find_render_service("token", "branch", "owner", "env")
    body = json.loads(requests[1].content)
    assert body["ownerId"] == "owner"
    assert body["environmentId"] == "env"
    assert body["envVars"] == [{"key": "PYTHON_VERSION", "value": "3.12.7"}]
    assert result["onrender_cname_target"] == "actual.onrender.com"


def test_existing_service_wrong_environment_fails_closed(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "service": {
                        "name": deploy.SERVICE_NAME,
                        "ownerId": "owner",
                        "environmentId": "other",
                        "repo": deploy.REPO_URL,
                        "branch": "branch",
                    }
                }
            ],
        )

    original_client = httpx.Client
    monkeypatch.setattr(
        deploy.httpx,
        "Client",
        lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(RuntimeError, match="Existing service"):
        deploy.provision_or_find_render_service("token", "branch", "owner", "env")
    assert len(requests) == 1


def test_failed_domain_creation_is_not_reported_as_success(monkeypatch):
    def handler(request):
        if request.url.path == "/v1/services":
            return httpx.Response(
                200,
                json=[
                    {
                        "service": {
                            "name": deploy.SERVICE_NAME,
                            "id": "srv-test",
                            "ownerId": "owner",
                            "environmentId": "env",
                            "repo": deploy.REPO_URL,
                            "branch": "branch",
                            "serviceDetails": {"url": "https://example.onrender.com"},
                        }
                    }
                ],
            )
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(403, json={"message": "denied"})

    original_client = httpx.Client
    monkeypatch.setattr(
        deploy.httpx,
        "Client",
        lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(httpx.HTTPStatusError):
        deploy.provision_or_find_render_service("token", "branch", "owner", "env")
