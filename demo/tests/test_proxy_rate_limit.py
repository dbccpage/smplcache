import importlib

from fastapi.testclient import TestClient

api = importlib.import_module("demo.api.app")


def test_render_uses_edge_client_ip_and_ignores_forwarded_for(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr(api, "execute_scenario_run", lambda cfg: {})
    client = TestClient(api.create_app(trust_render_edge=True))
    payload = {"scenario_id": "live_revenue"}
    assert (
        client.post(
            "/api/runs",
            json=payload,
            headers={"cf-connecting-ip": "203.0.113.1", "x-forwarded-for": "1.1.1.1"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/runs",
            json=payload,
            headers={"cf-connecting-ip": "203.0.113.1", "x-forwarded-for": "8.8.8.8"},
        ).status_code
        == 429
    )
    assert (
        client.post(
            "/api/runs", json=payload, headers={"cf-connecting-ip": "203.0.113.2"}
        ).status_code
        == 200
    )


def test_direct_mode_does_not_trust_client_supplied_edge_header(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr(api, "execute_scenario_run", lambda cfg: {})
    client = TestClient(api.create_app(trust_render_edge=False))
    payload = {"scenario_id": "live_revenue"}
    assert (
        client.post(
            "/api/runs", json=payload, headers={"cf-connecting-ip": "203.0.113.1"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/runs", json=payload, headers={"cf-connecting-ip": "203.0.113.2"}
        ).status_code
        == 429
    )
