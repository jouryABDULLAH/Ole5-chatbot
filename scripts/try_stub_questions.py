"""The four questions in docs/test_stub.md, each in a new session against the real model, printing each reply as it comes."""
import os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["TRACING"] = "false"

from ole5_chat import sessions, sheets

EMAIL = next(str(r["email"]) for r in sheets.customers()
             if str(r.get("product") or "").upper() == "OLE5" and str(r.get("active") or "").lower() == "yes")

QUESTIONS = [
    "كيف احفظ الرسالة كمسودة بدلًا من إرسالها",
    "كيف انشئ علامة مائية في النظام",
    "هل يدعم النظام بيانات التسليم للمراسلات الورقية؟",
    "هل أستطيع ترتيب المراسلات حسب تاريخ آخر إجراء عليها؟",
]

for n, text in enumerate(QUESTIONS, 1):
    sid, _ = sessions.start(EMAIL)
    started = time.perf_counter()
    state = sessions.send(sid, text)
    print(f"\n=== Question {n} ===")
    print(f"Customer: {text}")
    print(f"Bot: {state['messages'][-1].text}")
    print(f"  sources={state['messages'][-1].additional_kwargs.get('sources', [])}  time={time.perf_counter() - started:.1f}s", flush=True)
