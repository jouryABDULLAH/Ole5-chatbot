"""The customer's tickets: the rows of tickets.xlsx for their email, each with its state from the Ticket API (None when the API cannot say). Used by the tickets endpoint and the status handler; the voice channel reads the numbers."""

from ole5_chat import sheets
from ole5_chat.tickets.client import TicketApi, TicketApiError


def _rows(email: str) -> list[dict]:
    wanted = email.casefold()
    return [r for r in sheets.tickets() if str(r.get("customer_email") or "").casefold() == wanted]


def numbers(email: str) -> list[str]:
    """The customer's ticket numbers, from the sheet alone (no Ticket API call)."""
    return [str(r["ticket_number"]) for r in _rows(email)]


def my_tickets(email: str, api: TicketApi) -> list[dict]:
    tickets = []
    for r in _rows(email):
        try:
            state = api.get(str(r["ticket_id"])).get("state")
        except TicketApiError:
            state = None
        tickets.append({"ticket_number": str(r["ticket_number"]), "title": str(r.get("title") or ""),
                        "product": str(r.get("product") or ""), "created_at": str(r.get("created_at") or ""),
                        "state": state})
    return tickets
