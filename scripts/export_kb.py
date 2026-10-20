"""Exports the knowledge base's indexed text, read-only through RAGent2: one Markdown file per document in kb_export/<tenant>/, chunks in their original order. These are not the original files: RAGent2 keeps only the indexed chunks, and chunks under 10 words are skipped.

Usage:
  python scripts/export_kb.py                                  exports every document
  python scripts/export_kb.py --doc "<name>"                   exports one document (the name exactly as list_kb.py shows it)
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ragent2.evaluation.collect import collect_chunks

from ole5_chat.config import get_settings
from ole5_chat.knowledge import retriever


def file_name(document: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", document).strip() or "unnamed"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the knowledge base's indexed text to Markdown.")
    parser.add_argument("--tenant", help="tenant to export; defaults to the first one in KB_TENANTS")
    parser.add_argument("--doc", help="export only this document, named exactly as list_kb.py shows it")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    tenant = args.tenant or sorted(set(get_settings().kb_tenants.values()))[0]
    rag = retriever.ragent()

    doc_ids = None
    if args.doc:
        doc_ids = [d.doc_id for d in retriever.tenant(tenant).list() if d.name == args.doc]
        if not doc_ids:
            sys.exit(f"No document named {args.doc!r} in tenant {tenant!r}. Run scripts/list_kb.py to see the names.")

    chunks, _ = collect_chunks(rag.client, tenant, doc_ids, settings=rag.settings)
    documents: dict[str, list[dict]] = {}
    for chunk in chunks:
        documents.setdefault(chunk["metadata"].get("document_name") or "unknown", []).append(chunk)

    folder = ROOT / "kb_export" / tenant
    folder.mkdir(parents=True, exist_ok=True)
    for name, items in sorted(documents.items()):
        items.sort(key=lambda c: (str(c["metadata"].get("chunk_id") or ""), c["point_id"]))
        lines = [f"# {name}", ""]
        for c in items:
            page = c["metadata"].get("page_number")
            lines += [f"_{c['metadata'].get('chunk_id') or c['point_id']}" + (f", page {page}_" if page else "_"), "",
                      c["page_content"].strip(), ""]
        path = folder / f"{file_name(name)}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"{len(items):>5} chunks  {path.relative_to(ROOT)}")
    print(f"\n{len(documents)} documents exported to {folder.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
