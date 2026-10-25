"""Three conversations through the graph, printing the reply, phase and pending after each turn."""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["TRACING"] = "false"

from ole5_chat import sessions, sheets

EMAIL = next(str(r["email"]) for r in sheets.customers()
             if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")

CONVERSATIONS = [
    ["هل يمكن جدولة إرسال مراسلة في وقت لاحق؟", "ايه"],
    ["هل يمكن جدولة إرسال مراسلة في وقت لاحق؟", "كيف أضيف علامة مائية؟", "لا شكراً"],
    ["هل يمكن جدولة إرسال مراسلة في وقت لاحق؟", "هل يتكامل النظام مع Microsoft Teams؟", "لا", "ايه"],
]

for n, turns in enumerate(CONVERSATIONS, 1):
    sid, _ = sessions.start(EMAIL)
    print(f"\n=== Conversation {n} ===")
    for text in turns:
        state = sessions.send(sid, text)
        print(f"\nCustomer: {text}")
        print(f"Bot: {state['messages'][-1].text}")
        print(f"  phase={state['phase']}  next={state.get('next')}  "
              f"pending={[p.text for p in state['pending']]}  draft={'yes' if state.get('ticket_draft') else 'no'}")