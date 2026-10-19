"""A stand-in for the Ticket API with the same three calls, used while TICKET_API_URL is empty. Tickets live in memory and every call is logged and recorded."""

import itertools
import logging
import uuid
from datetime import datetime

log = logging.getLogger(__name__)


class MockTicketClient:
    def __init__(self):
        self.tickets: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self._counter = itertools.count(1)

    def create(self, ticket: dict, article: dict) -> dict:
        self.calls.append(("TicketCreate", {"Ticket": ticket, "Article": article}))
        ticket_id = str(uuid.uuid4())
        number = f"T-MOCK-{datetime.now():%Y%m%d%H%M%S}-{next(self._counter):04d}"
        self.tickets[ticket_id] = {"number": number, "state": "new", "queue": ticket.get("Queue")}
        log.info("tickets  mock TicketCreate number=%s queue=%s type=%s priority=%s", number, ticket.get("Queue"),
                 ticket.get("Type"), ticket.get("Priority"))
        return {"TicketID": ticket_id, "TicketNumber": number}

    def update(self, ticket_id: str, dynamic_fields: list[dict], article: dict) -> dict:
        self.calls.append(("TicketUpdate", {"TicketID": ticket_id, "DynamicField": dynamic_fields, "Article": article}))
        log.info("tickets  mock TicketUpdate id=%s fields=%s", ticket_id, [f["Name"] for f in dynamic_fields])
        return {"TicketID": ticket_id}

    def get(self, ticket_id: str) -> dict:
        self.calls.append(("TicketGet", {"TicketID": ticket_id}))
        ticket = self.tickets.get(ticket_id, {})
        return {"state": ticket.get("state"), "queue": ticket.get("queue")}
