"""Customer sign-in: who the customer is, checked against customers.xlsx. Nothing a request body says about the customer is trusted."""

from pydantic import BaseModel, ConfigDict

from ole5_chat import sheets

NOT_REGISTERED = "This email is not registered for support chat"


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    email: str
    name: str
    organization: str
    product: str
    language: str


class NotRegistered(Exception):
    """The email is not in customers.xlsx, or is not active."""


def _clean(value) -> str:
    return str(value or "").strip()


def lookup(email: str) -> Identity:
    wanted = _clean(email).casefold()
    for row in sheets.customers():
        if _clean(row.get("email")).casefold() == wanted and _clean(row.get("active")).casefold() == "yes":
            return Identity(
                customer_id=_clean(row["email"]).casefold(),
                email=_clean(row["email"]),
                name=_clean(row.get("name")),
                organization=_clean(row.get("organization")),
                product=_clean(row.get("product")).upper(),
                language=_clean(row.get("language")).lower() or "en",
            )
    raise NotRegistered(NOT_REGISTERED)
