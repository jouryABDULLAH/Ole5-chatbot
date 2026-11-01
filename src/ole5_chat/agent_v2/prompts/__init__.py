"""One prompt module per component in the new graph."""

from .finalize import FINALIZE_PROMPT
from .router import router_prompt
from .ticket_manager import manager_prompt
from .ticket_status import ticket_status_prompt
from .validate import VALIDATE_PROMPT

__all__ = ["FINALIZE_PROMPT", "VALIDATE_PROMPT", "manager_prompt",
           "router_prompt", "ticket_status_prompt"]
