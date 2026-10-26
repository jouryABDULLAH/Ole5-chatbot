"""The one function the agent calls to search the knowledge base: RAGent2 when KB_BACKEND is ragent2 and the product has a tenant, the stand-in otherwise."""

from ole5_chat import llm
from ole5_chat.config import get_settings
from ole5_chat.knowledge import retriever, stub
from ole5_chat.knowledge.contracts import Retrieval, Verdict


def search(question: str, product: str) -> Retrieval:
    s = get_settings()
    if s.kb_down_flag.exists():  # the failure demo (VOICE_PLAN.md section 10, scenario 5)
        return Retrieval(query=question, verdict=Verdict.NOT_IN_KB, error="knowledge base switched off (.kb_down)")
    tenant = s.kb_tenants.get(product.upper())
    if s.kb_backend == "ragent2" and tenant:
        return retriever.retrieve(question, tenant, voice=llm.voice_active())
    return stub.search(question)
