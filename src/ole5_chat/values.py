"""The value lists a ticket is classified with, per product. Read from values.xlsx now; only catalog() changes when the options API arrives."""

from pydantic import BaseModel, ConfigDict

from ole5_chat import sheets


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    parent: str | None = None
    description: str | None = None


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str
    service: str
    queues: list[Option]
    types: list[Option]
    subtypes: list[Option]
    reasons: list[Option]
    priorities: list[Option]
    statuses: list[Option]

    @property
    def has_reason_catalog(self) -> bool:
        return bool(self.reasons)

    def subtypes_of(self, type_: str) -> list[Option]:
        return [o for o in self.subtypes if (o.parent or "").casefold() == type_.casefold()]

    def reason_for(self, subtype: str) -> str | None:
        return next((o.value for o in self.reasons if (o.parent or "").casefold() == subtype.casefold()), None)


class UnknownProduct(KeyError):
    """No values.xlsx rows for this product code."""


def _option(row: dict) -> Option:
    def text(key):
        return str(row[key]).strip() if row.get(key) is not None else None
    return Option(value=text("value"), parent=text("parent"), description=text("description"))


def catalog(product: str) -> Catalog:
    code = product.upper()
    rows = sheets.values()
    mine = [r for r in rows if str(r.get("product") or "").upper() == code]
    shared = [r for r in rows if r.get("product") is None]
    service = next((r for r in mine if r["field"] == "product"), None)
    if service is None:
        raise UnknownProduct(code)

    def pick(source, field):
        return [_option(r) for r in source if r["field"] == field]

    return Catalog(
        product=code,
        service=str(service["value"]).strip(),
        queues=pick(mine, "queue"),
        types=pick(mine, "type"),
        subtypes=pick(mine, "subtype"),
        reasons=pick(mine, "reason"),
        priorities=pick(shared, "priority"),
        statuses=pick(shared, "status"),
    )
