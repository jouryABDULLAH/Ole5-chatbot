"""The customer's tickets: the rows of tickets.xlsx for their email, each with its state from the Ticket API (None when the API cannot say). Used by the tickets endpoint and the status handler."""

from ole5_chat import sheets
from ole5_chat.tickets.client import TicketApi, TicketApiError


def my_tickets(email: str, api: TicketApi) -> list[dict]:
    wanted = email.casefold()
    rows = [r for r in sheets.tickets() if str(r.get("customer_email") or "").casefold() == wanted]
    tickets = []
    for r in rows:
        try:
            state = api.get(str(r["ticket_id"])).get("state")
        except TicketApiError:
            state = None
        tickets.append({"ticket_number": str(r["ticket_number"]), "title": str(r.get("title") or ""),
                        "product": str(r.get("product") or ""), "created_at": str(r.get("created_at") or ""),
                        "state": state})
    return tickets
