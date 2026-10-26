"""Talk and question conversations through the graph, printing the reply, next, phase, pending, sources, corrections and time after each turn."""
import os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["TRACING"] = "false"

from ole5_chat import sessions, sheets

EMAIL = next(str(r["email"]) for r in sheets.customers()
             if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")

CONVERSATIONS = [
    ["من انت"],
    ["وش تقدر تسوي؟"],
    ["what can you do?"],
    ["عندي مشكلة", "no", "nothing", "i have no problems!", "شكراً"],
    ["كيف اضيف علامة مائية؟ وكيف اعيد تعيين كلمة المرور؟"],
]

for n, turns in enumerate(CONVERSATIONS, 1):
    sid, _ = sessions.start(EMAIL)
    print(f"\n=== Conversation {n} ===")
    for text in turns:
        started = time.perf_counter()
        before = len(sessions.get(sid)["corrections"])
        state = sessions.send(sid, text)
        nxt = next((h.values.get("next") for h in sessions._graph.get_state_history(sessions._config(sid))
                    if h.values.get("next")), None)
        print(f"\nCustomer: {text}")
        print(f"Bot: {state['messages'][-1].text}")
        print(f"  next={nxt}  phase={state['phase']}  pending={[p.text for p in state['pending']]}  "
              f"sources={state['sources']}  corrections={state['corrections'][before:]}  "
              f"time={time.perf_counter() - started:.1f}s")
