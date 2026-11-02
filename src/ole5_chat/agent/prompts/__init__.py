"""One prompt module per component in the graph."""

from .router import router_prompt
from .ticket_operator import manager_prompt
from .ticket_enquiry import ticket_enquiry_prompt

__all__ = ["manager_prompt", "router_prompt", "ticket_enquiry_prompt"]
