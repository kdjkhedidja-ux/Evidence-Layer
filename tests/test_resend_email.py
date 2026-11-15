import asyncio

import httpx

from evidence_service.resend_email import send_invitation_email

TOKEN = "synthetic-invitation-token-value-1234"
FROM = "noreply@evidencelayer.dev"
BASE = "https://evidencelayer.dev"
KEY = "re_synthetic_send_only_key"


def test_resend_invitation_contains_the_one_time_link():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"id": "synthetic"})

    # The module constructs its own client; monkeypatch the client class to
    # inject the mock transport without changing production behavior.
    import evidence_service.resend_email as module

    original = module.httpx.AsyncClient
    module.httpx.AsyncClient = lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **{k: v for k, v in kwargs.items() if k != "transport"}
    )
    try:
        result = asyncio.run(
            send_invitation_email(
                api_key=KEY, from_email=FROM, base_url=BASE,
                to_email="member@example.test", token=TOKEN,
            )
        )
    finally:
        module.httpx.AsyncClient = original
    assert result is True
    assert seen["path"] == "/emails"
    assert TOKEN.encode() in seen["body"]
    assert b"noreply@evidencelayer.dev" in seen["body"]


def test_resend_failure_is_reported_without_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "synthetic"})

    import evidence_service.resend_email as module

    original = module.httpx.AsyncClient
    module.httpx.AsyncClient = lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **{k: v for k, v in kwargs.items() if k != "transport"}
    )
    try:
        result = asyncio.run(
            send_invitation_email(
                api_key=KEY, from_email=FROM, base_url=BASE,
                to_email="member@example.test", token=TOKEN,
            )
        )
    finally:
        module.httpx.AsyncClient = original
    assert result is False


def test_missing_resend_configuration_does_not_send():
    assert asyncio.run(
        send_invitation_email(
            api_key="", from_email=FROM, base_url=BASE,
            to_email="member@example.test", token=TOKEN,
        )
    ) is False
