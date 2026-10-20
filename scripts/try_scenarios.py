"""Runs four conversations against the real model (Groq) and prints each turn with the step and the ticket draft. The knowledge base is the stand-in unless --kb ragent2 is given; nothing is submitted. Usage: python scripts/try_scenarios.py [--email you@example.com] [--kb stub|ragent2] [--only 1,3]"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SCENARIOS = {
    1: ("Greeting and a covered question in one message, then thanks",
        ["مرحبا، نسيت كلمة المرور كيف أستعيدها؟", "شكراً"]),
    2: ("Uncovered question, yes to the ticket, details over two messages",
        ["The mobile app closes by itself when I open the Inbox", "yes please",
         "It started yesterday, on Android", "The error says 'Unexpected error'"]),
    3: ("A covered question in the middle of collecting, then the last detail",
        ["I want to open a ticket, the app crashes when I open the Inbox", "by the way, how do I reset my password?",
         "Android, it started this morning"]),
    4: ("The customer does not know a detail, then cancels at the summary",
        ["I need help migrating our old data", "yes", "I don't know the volume, just open it", "cancel it"]),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", help="an active OLE5 customer in sheets/customers.xlsx (default: the first one)")
    parser.add_argument("--kb", choices=["stub", "ragent2"], default="stub")
    parser.add_argument("--only", help="comma-separated scenario numbers")
    args = parser.parse_args()
    os.environ["KB_BACKEND"] = args.kb
    os.environ["TRACING"] = "false"
    os.environ["TICKET_API_URL"] = ""

    logging.basicConfig(level=logging.INFO, format="    %(message)s")
    for name in ("httpx", "httpcore", "groq", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    from ole5_chat import sessions, sheets

    email = args.email or next(str(r["email"]) for r in sheets.customers()
                               if str(r.get("product") or "").upper() == "OLE5"
                               and str(r.get("active") or "").lower() == "yes")
    chosen = [int(n) for n in args.only.split(",")] if args.only else list(SCENARIOS)

    for number in chosen:
        title, turns = SCENARIOS[number]
        print(f"\n=== {number}. {title} ({email}, kb={args.kb})")
        sid, state = sessions.start(email)
        print(f"BOT  [{state['phase']}] {state['messages'][-1].text}")
        for text in turns:
            print(f"\nCUSTOMER  {text}")
            state = sessions.send(sid, text)
            draft = state.get("ticket_draft")
            print(f"BOT  [{state['phase']}] {state['messages'][-1].text}")
            if state["sources"]:
                print(f"     sources: {state['sources']}")
            if draft:
                print(f"     draft: collected={draft.collected} missing={draft.missing} unknown={draft.unknown} "
                      f"ready={draft.classified}")
                if draft.classified:
                    print(f"     summary: {draft.title} | {draft.queue} / {draft.type} / {draft.subtype} / "
                          f"{draft.priority}")
            else:
                print("     draft: none")
        if state["corrections"]:
            print(f"     corrections: {state['corrections']}")


if __name__ == "__main__":
    main()
