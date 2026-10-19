"""Signs in to the ds.t2.sa gateway in front of Qdrant: posts the email and password from .env as form data, sends the session cookie with every Qdrant request, and signs in again when the gateway answers 401."""

import logging
import threading
from collections.abc import Callable, Generator

import httpx

from ole5_chat.config import get_settings

log = logging.getLogger(__name__)


class LoginFailed(RuntimeError):
    """The gateway did not accept the credentials or did not return a session cookie."""


def _reason(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return "no JSON body"
    if isinstance(detail, list):
        return "; ".join(f"{'.'.join(str(p) for p in d.get('loc', []))}: {d.get('msg')}" for d in detail)
    return str(detail)


def login() -> str:
    s = get_settings()
    if not s.qdrant_login_email or not s.qdrant_login_password:
        raise LoginFailed("QDRANT_LOGIN_EMAIL and QDRANT_LOGIN_PASSWORD must be set in .env")
    response = httpx.post(s.qdrant_login_url, timeout=30, data={
        "email": s.qdrant_login_email, "password": s.qdrant_login_password.get_secret_value()})
    if response.status_code != 200:
        raise LoginFailed(f"login returned {response.status_code}: {_reason(response)}")
    cookie = response.cookies.get(s.qdrant_session_cookie)
    if not cookie:
        raise LoginFailed(f"login returned no {s.qdrant_session_cookie} cookie")
    log.info("qdrant  signed in to the gateway as %s", s.qdrant_login_email)
    return cookie


class SessionLogin(httpx.Auth):
    def __init__(self, cookie_name: str, sign_in: Callable[[], str] = login):
        self._cookie_name = cookie_name
        self._sign_in = sign_in
        self._cookie: str | None = None
        self._lock = threading.Lock()

    def _refresh(self, stale: str | None) -> str:
        with self._lock:
            if self._cookie is None or self._cookie == stale:
                self._cookie = self._sign_in()
            return self._cookie

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        cookie = self._cookie or self._refresh(None)
        request.headers["Cookie"] = f"{self._cookie_name}={cookie}"
        response = yield request
        if response.status_code == 401:
            log.info("qdrant  session expired, signing in again")
            request.headers["Cookie"] = f"{self._cookie_name}={self._refresh(cookie)}"
            yield request
