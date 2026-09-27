"""The UI's origin must be allowed, or every API call fails as "Network Error".

The allowlist was hardcoded to localhost:3000 and localhost:5173. docker-compose
publishes the UI on 127.0.0.1:3000, and a browser treats 127.0.0.1 and localhost
as different origins even though they are the same machine -- so a user who
opened the address the compose file suggests had every request blocked. axios
reports a CORS block as a bare "Network Error", and the onboarding profile
dropdown then falls back to its "default (account unavailable)" placeholder, so
the symptom pointed at AWS rather than at the browser.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings

pytestmark = [pytest.mark.unit]

PROBE = "/api/v1/onboarding/iam/profiles"


@pytest.fixture(scope="module")
def client():
    import app.main

    return TestClient(app.main.app)


def _preflight(client, origin: str):
    return client.options(
        PROBE,
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",   # what docker-compose publishes
        "http://localhost:5173",
        "http://127.0.0.1:5173",   # vite dev server, same trap
    ],
)
def test_default_allowlist_covers_both_spellings_of_the_local_host(client, origin):
    response = _preflight(client, origin)

    assert response.status_code == 200, f"{origin} was refused at preflight"
    assert response.headers.get("access-control-allow-origin") == origin


def test_unlisted_origins_are_still_refused(client):
    response = _preflight(client, "http://attacker.example")

    assert response.headers.get("access-control-allow-origin") is None


def test_origins_are_configurable_for_other_hostnames(monkeypatch):
    # Anyone serving the UI from a hostname or behind a proxy needs this;
    # without it they hit exactly the same dead end.
    monkeypatch.setenv("MAXOPS_CORS_ORIGINS", "https://maxops.corp.example, http://10.0.0.5:3000")

    assert Settings().cors_origin_list == [
        "https://maxops.corp.example",
        "http://10.0.0.5:3000",
    ]


def test_blank_entries_are_dropped(monkeypatch):
    monkeypatch.setenv("MAXOPS_CORS_ORIGINS", "http://a.example,,  ,http://b.example")

    assert Settings().cors_origin_list == ["http://a.example", "http://b.example"]
