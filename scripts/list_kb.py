"""Shows what the knowledge base holds, read-only through RAGent2 and signed in through the gateway like the chat.

Usage:
  python scripts/list_kb.py                                    lists the documents
  python scripts/list_kb.py --query "..."                      shows the passages that best match a query
  python scripts/list_kb.py --query "..." --doc "<name>"       only from one document (the name exactly as listed)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ole5_chat.config import get_settings
from ole5_chat.knowledge import retriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only view of the knowledge base.")
    parser.add_argument("--tenant", help="tenant to read; defaults to the first one in KB_TENANTS")
    parser.add_argument("--query", help="show the passages that best match this query")
    parser.add_argument("--doc", help="restrict --query to one document, named exactly as listed")
    parser.add_argument("--top", type=int, default=5, help="how many passages to show (default 5)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    name = args.tenant or sorted(set(get_settings().kb_tenants.values()))[0]
    tenant = retriever.tenant(name)

    if not args.query:
        documents = sorted(tenant.list(), key=lambda d: d.name or "")
        print(f"{len(documents)} documents in tenant {name!r}:")
        for d in documents:
            print(f"  {d.name}  ({d.doc_id[:12]})")
        return

    passages = tenant.search(args.query, top_k=args.top, document_name=args.doc)
    print(f"{len(passages)} passages for {args.query!r} in tenant {name!r}"
          + (f", document {args.doc!r}" if args.doc else ""))
    for i, p in enumerate(passages, 1):
        page = f" p.{p.page_number}" if p.page_number else ""
        print(f"\n[{i}] {p.document_name}{page}  score={p.score:.3f}\n{p.text.strip()}")


if __name__ == "__main__":
    main()
