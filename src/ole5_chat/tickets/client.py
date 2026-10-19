"""The T2 Ticket API: TicketCreate, TicketUpdate and TicketGet, posted to <TICKET_API_URL>/TicketAPI/<operation> with the service account in the body, as the requirements show them. ticket_client() returns the mock while TICKET_API_URL is empty."""

import logging
from functools import lru_cache
from typing import Protocol

import httpx

from ole5_chat.config import get_settings

log = logging.getLogger(__name__)


class TicketApiError(RuntimeError):
    """The Ticket API refused the call or could not be reached."""


class TicketApi(Protocol):
    def create(self, ticket: dict, article: dict) -> dict: ...
    def update(self, ticket_id: str, dynamic_fields: list[dict], article: dict) -> dict: ...
    def get(self, ticket_id: str) -> dict: ...


class TicketClient:
    def __init__(self, base_url: str, user: str, password: str):
        self._base = base_url.rstrip("/")
        self._user = user
        self._password = password

    def _call(self, operation: str, body: dict) -> dict:
        try:
            response = httpx.post(f"{self._base}/TicketAPI/{operation}", timeout=30,
                                  json={"UserLogin": self._user, "Password": self._password, **body})
        except httpx.HTTPError as exc:
            raise TicketApiError(f"{operation}: {type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise TicketApiError(f"{operation}: HTTP {response.status_code}")
        data = response.json()
        if data.get("Error"):
            raise TicketApiError(f"{operation}: {data['Error']}")
        return data

    def create(self, ticket: dict, article: dict) -> dict:
        data = self._call("TicketCreate", {"Ticket": ticket, "Article": article})
        return {"TicketID": data["TicketID"], "TicketNumber": data["TicketNumber"]}

    def update(self, ticket_id: str, dynamic_fields: list[dict], article: dict) -> dict:
        return self._call("TicketUpdate", {"TicketID": ticket_id, "DynamicField": dynamic_fields, "Article": article})

    def get(self, ticket_id: str) -> dict:
        ticket = (self._call("TicketGet", {"TicketID": ticket_id}).get("Ticket") or [{}])[0]
        return {"state": ticket.get("State"), "queue": ticket.get("Queue")}


@lru_cache(maxsize=1)
def ticket_client() -> TicketApi:
    from ole5_chat.tickets.mock import MockTicketClient

    s = get_settings()
    if not s.ticket_api_url:
        log.info("tickets  TICKET_API_URL is empty: using the mock Ticket API")
        return MockTicketClient()
    password = s.ticket_api_password.get_secret_value() if s.ticket_api_password else ""
    return TicketClient(s.ticket_api_url, s.ticket_api_user or "", password)
