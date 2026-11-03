"""Shared state for the owner-routing graph."""

from typing import Literal, TypedDict

from langgraph.graph import MessagesState

from ole5_chat.agent.contracts import (
    PendingCustomerResponse,
    NextTarget,
    Route,
    ResponsePacket,
    SearchOutcome,
    ValidationOutcome,
    WorkerName,
)
from ole5_chat.identity import Identity


class AgentState(MessagesState, total=False):
    input_event: Literal["message", "submit"] | None
    # Persistent conversation/workflow state.
    identity: Identity
    language: str | None

    active_issue: dict | None
    pending_issues: list[dict]
    ticket_draft: dict | None
    pending_customer_response: PendingCustomerResponse | None
    
    summary_shown: bool
    submission_requested: bool
    draft_version: int | None
    summary_delivered_version: int | None
    summary_confirmed_version: int | None
    attachments: list[dict]
    created_tickets: list[dict]
    
    last_answer_evidence: dict | None
    sources: list[str]
    source_details: list[dict]

    # Current-turn routing and workflow state.
    intent: Route | None
    next_target: NextTarget | None
    last_worker: WorkerName | None
    response_packet: ResponsePacket | None
    route_reason: str | None
    explicit_ticket_request: bool
    
    current_request: str | None
    
    search_scope: Literal["product", "outside_scope"] | None
    search_result: dict | None
    current_references: list[dict]
    search_outcome: SearchOutcome | None
    unresolved_information: dict | None
    
    ticket_result: dict | None
    ticket_operator_result: dict | None
    ticket_enquiry_result: dict | None
    
    manager_action: str | None
    manager_reason: str | None
    
    answered_detail: PendingCustomerResponse | None
    grounding_failed: bool
    reply: str | None
    
    validation_outcome: ValidationOutcome | None
    validation_reason: str | None
    validation_note: str | None
    validation_retries: int


def initial_state(identity: Identity) -> AgentState:
    """Create a clean state for the graph without old-agent fields."""

    return AgentState(
        messages=[],
        input_event=None,
        identity=identity,
        language=None,
        active_issue=None,
        pending_issues=[],
        ticket_draft=None,
        pending_customer_response=None,
        summary_shown=False,
        submission_requested=False,
        draft_version=None,
        summary_delivered_version=None,
        summary_confirmed_version=None,
        attachments=[],
        created_tickets=[],
        last_answer_evidence=None,
        sources=[],
        source_details=[],
        intent=None,
        next_target=None,
        last_worker=None,
        response_packet=None,
        route_reason=None,
        explicit_ticket_request=False,
        current_request=None,
        search_scope=None,
        search_result=None,
        current_references=[],
        search_outcome=None,
        unresolved_information=None,
        ticket_result=None,
        ticket_operator_result=None,
        ticket_enquiry_result=None,
        manager_action=None,
        manager_reason=None,
        answered_detail=None,
        grounding_failed=False,
        reply=None,
        validation_outcome=None,
        validation_reason=None,
        validation_note=None,
        validation_retries=0,
    )



