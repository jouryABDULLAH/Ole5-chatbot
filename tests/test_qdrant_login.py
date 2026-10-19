"""Signing in to the gateway in front of Qdrant, with the real Qdrant client and a fake gateway: the cookie is sent, and an expired session signs in again once."""

import httpx
from qdrant_client import QdrantClient

from ole5_chat.knowledge.qdrant_login import SessionLogin


def gateway(valid: dict):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie", "")
        seen.append(cookie)
        if cookie != f"ole5_session={valid['cookie']}":
            return httpx.Response(401, json={"detail": "not signed in"})
        return httpx.Response(200, json={"status": "ok", "time": 0, "result": {"exists": True}})

    return handler, seen


def client(auth, handler):
    return QdrantClient(url="https://gw.test:443/ole5_agent/qdrant", auth=auth, check_compatibility=False,
                        transport=httpx.MockTransport(handler))


def test_signs_in_once_and_sends_the_cookie():
    sign_ins = []
    valid = {"cookie": "c1"}

    def sign_in():
        sign_ins.append(1)
        return valid["cookie"]

    handler, seen = gateway(valid)
    qdrant = client(SessionLogin("ole5_session", sign_in), handler)
    assert qdrant.collection_exists("default") is True
    assert qdrant.collection_exists("default") is True
    assert len(sign_ins) == 1
    assert seen == ["ole5_session=c1", "ole5_session=c1"]


def test_expired_session_signs_in_again_and_retries():
    cookies = iter(["old", "new"])
    valid = {"cookie": "new"}
    handler, seen = gateway(valid)
    qdrant = client(SessionLogin("ole5_session", lambda: next(cookies)), handler)
    assert qdrant.collection_exists("default") is True
    assert seen == ["ole5_session=old", "ole5_session=new"]


def test_validation_error_names_the_field_but_never_the_value():
    from ole5_chat.knowledge.qdrant_login import _reason
    body = {"detail": [{"loc": ["body", "email"], "msg": "value is not a valid email address",
                        "input": "secret-value", "type": "value_error"}]}
    reason = _reason(httpx.Response(422, json=body))
    assert reason == "body.email: value is not a valid email address"
    assert "secret-value" not in reason
