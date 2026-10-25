"""Three urgent-ticket conversations through the graph, printing the reply, phase, pending, the draft and the tickets created after each turn."""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["TRACING"] = "false"

from ole5_chat import sessions, sheets

EMAIL = next(str(r["email"]) for r in sheets.customers()
             if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")

# TICKET_API_URL unset in .env: ticket_client() is the mock, so nothing reaches the real ticket system.
URGENT = "ولا موظف يقدر يسجل دخول"
CONVERSATIONS = [
    ["ولا موظف عندنا يقدر يسجل دخول من الصبح", "ارسلها"],
    ["هل يمكن جدولة إرسال مراسلة في وقت لاحق؟", "ايه", "النسخة 17", f"بعد {URGENT}", "ألغ التذكرة الأولى", "ارسلها"],
    ["هل يمكن جدولة إرسال مراسلة في وقت لاحق؟", "ايه", "هذا كل شيء", URGENT, "ارسلها", "ارسلها"],
]

for n, turns in enumerate(CONVERSATIONS, 1):
    sid, _ = sessions.start(EMAIL)
    print(f"\n=== Conversation {n} ===")
    for text in turns:
        state = sessions.send(sid, text)
        print(f"\nCustomer: {text}")
        print(f"Bot: {state['messages'][-1].text}")
        draft = state.get("ticket_draft")
        print(f"  phase={state['phase']}  pending={[p.text for p in state['pending']]}  "
              f"draft={'no' if draft is None else 'classified' if draft.classified else 'yes'}  "
              f"urgent={draft.urgent if draft else '-'}  missing={draft.missing if draft else '-'}  "
              f"tickets={[t['ticket_number'] for t in state['created_tickets']]}")