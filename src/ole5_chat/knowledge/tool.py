"""The knowledge base as a tool for a model that searches for itself. Ported from the support agent's _make_kb_tool: every Retrieval goes to a sink the node reads after the loop; the model gets a trimmed view of the same thing."""

import json
import logging

from langchain_core.tools import tool

from ole5_chat import products
from ole5_chat.knowledge.contracts import Retrieval
from ole5_chat.knowledge.search import search

log = logging.getLogger(__name__)

# How much of an INFERENTIAL passage to hand back to the model: enough to judge
# it by, short enough that four searches do not fill the context. DIRECT passages
# go whole: the reply is written from them and the grounding check reads them in
# full, so the model must see every word the check counts as grounded.
PASSAGE_CHARS = 1200


def make_search_tool(product: str, sink: list[Retrieval]):
    """The knowledge base as a tool, appending every result to a sink.

    The sink is what the node reads. What the model gets back is a trimmed view
    of the same thing.
    """

    @tool
    def search_knowledge_base(question: str) -> str:
        """Search the {product} knowledge base.

        Ask one clear question in your own words, not the customer's raw text.
        Search once per question the customer asks; the search already rephrases
        your question and tries several wordings.
        """
        result = search(question, product)
        sink.append(result)
        log.info("search_knowledge_base  query=%r verdict=%s evidence=%s error=%s", question,
                 result.verdict.value if result.verdict else None,
                 [(e.document, e.section, e.relation) for e in result.evidence],
                 (result.error or None) and result.error[:300])
        if result.error:
            return json.dumps({
                "status": "search_failed",
                "detail": result.error[:300],
                "note": "The knowledge base could not be reached. This does NOT "
                        "mean the answer is absent. Do not treat it as not covered.",
            }, ensure_ascii=False)
        return json.dumps(
            {
                "verdict": result.verdict.value,
                "evidence": [
                    {
                        "document": e.document,
                        "section": e.section,
                        "relation": e.relation,
                        "why": e.reason,
                        "text": e.text if e.relation == "DIRECT" else e.text[:PASSAGE_CHARS],
                    }
                    for e in result.evidence
                ],
                "error": result.error,
            },
            ensure_ascii=False,
        )

    search_knowledge_base.description = search_knowledge_base.description.replace(
        "{product}", products.load(product).name)
    return search_knowledge_base
