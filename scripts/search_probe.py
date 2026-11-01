import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ole5_chat import llm
from ole5_chat.agent_v2.prompts.search import search_prompt
from ole5_chat.identity import Identity
from ole5_chat.knowledge.tool import make_search_tool

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

state = {
    "identity": Identity(
        customer_id="test",
        email="ole5@example.com",
        name="Test",
        organization="Test",
        product="OLE5",
        language="ar",
    ),
    "messages": [HumanMessage(content="ليش عرضتها")],
    "current_request": "ليش عرضتها",
}

prompt = search_prompt(state)
tool = make_search_tool("OLE5", [])

for run in range(3):
    print(f"RUN {run + 1}")
    try:
        result = llm.invoke(
            [SystemMessage(content=prompt), *state["messages"]],
            tool,
            "required",
        )
        print("TOOL_CALLS", result.tool_calls)
        print("TEXT", result.text)
    except Exception as exc:
        print(type(exc).__name__, str(exc)[:1000])