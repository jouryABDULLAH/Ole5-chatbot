# Ole5 chat

A support chat for Ole5 and RiCH customers. It answers questions from the product's knowledge base, cites the documents it used, and prepares a support ticket when it has no answer or the problem needs the team. The service is a FastAPI app; the chat is a web component (`web/t2-chat.js`) that any page can embed and that talks only to the HTTP API.

## What you need

- Python 3.12 or newer
- A Groq API key (the model runs on Groq)
- `ragent2` package installed, and access to the T2 Qdrant service. Without them the service runs with a small built-in stub, which is enough to try the chat and the ticket flow.

## Install

1. Clone the repository and open a terminal in it.

2. Create a virtual environment and activate it.

   Windows (PowerShell):
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
   macOS or Linux:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Only if you use the real knowledge base: install `ragent2` from the package the team provides, for example:
   ```bash
   pip install path/to/ragent2-0.2.0-py3-none-any.whl

   pip install ragent2 --index-url https://t2development.pkgs.visualstudio.com/ResearchTeam_AIAgents/_packaging/AI/pypi/simple/   
   ```

5. Create the settings file from the example and fill it in:
   ```bash
   cp .env.example .env
   ```
   

   | Setting | What it is |
   |---|---|
   | `GROQ_API_KEY` | Required. The Groq key the model calls use. |
   | `KB_BACKEND` | `stub` (default) for the built-in passages, `ragent2` for the real knowledge base. |
   | `QDRANT_URL`, `QDRANT_API_KEY` | The Qdrant service behind RAGent2. Needed with `ragent2`. |
   | `QDRANT_LOGIN_EMAIL`, `QDRANT_LOGIN_PASSWORD` | Set these instead of the API key when Qdrant sits behind the T2 login. |
   | `TICKET_API_URL`, `TICKET_API_USER`, `TICKET_API_PASSWORD` | The T2 Ticket API. Leave the URL empty to use the mock. |
   | `TRACING` | `true` sends traces to Phoenix (see below). Default `false`. |
   | `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME` | Where the traces go and under which project name. |

   Every setting and its default is in `src/ole5_chat/config.py`.

6. Check who can sign in. The chat accepts an email only if it is in `sheets/customers.xlsx` with `active` set to `yes`. The sheet comes with two test customers:

   | Email | Product | Language |
   |---|---|---|
   | `ole5.test@example.com` | OLE5 | Arabic |
   | `customer@example.com` | RiCH | English |


## Run

Start the service from the repository root:

```bash
uvicorn ole5_chat.main:app --app-dir src --reload
```

Then open http://localhost:8000. The page stands in for the ticket system: the knowledge base documents are listed on the left and rendered in the page, and the chat opens at the bottom right. Sign in with one of the emails above, ask a question, and compare the answer with the document it cites. Add `?lang=ar` to the address to see the sign-in step in Arabic.

Notes:

- With `KB_BACKEND=ragent2` the first start loads the retrieval models in the background and logs `kb  ready` when searches can run.
- Attachments are stored in `uploads/`. Every ticket created, mock or real, is recorded in `sheets/tickets.xlsx`; the mock keeps the ticket itself in memory.

## Tracing with Phoenix (optional)

Phoenix shows every turn as a trace: the router's decision, each model call with its reasoning, the search results and the final reply. It is installed with the requirements. Start it in a second terminal:

```bash
phoenix serve
```

Set `TRACING=true` in `.env`, restart the service, and open http://localhost:6006.

## Tests

The unit tests run offline, with the model stubbed out:

```bash
pytest -q
```

To be sure no real call is made, set the key to a dummy value for the run, for example `GROQ_API_KEY=invalid pytest -q` in bash.

The router can also be checked against the real model on a fixed set of messages:

```bash
RUN_LIVE=1 pytest tests/test_router_live.py -s
```

## Project layout

| Path | What it holds |
|---|---|
| `src/ole5_chat/api.py` | The HTTP API: sessions, messages, attachments, submit, documents, tickets. |
| `src/ole5_chat/agent/` | The conversation graph: router, handlers, search, respond, validate. |
| `src/ole5_chat/knowledge/` | Knowledge search over RAGent2, and the stub. |
| `src/ole5_chat/tickets/` | The Ticket API client, its mock, submit and status lookup. |
| `products/<code>/chat.yaml` | Each product's persona, ticket topics and urgency rules. |
| `prompts/chat/*.md` | The system prompts of each model node. |
| `sheets/` | Customers, ticket field values, attachments and mock tickets, as Excel sheets. |
| `kb_export/` | The knowledge base documents as Markdown, served to the page. |
| `web/` | The stand-in page and the chat widget. |
| `evals/`, `scripts/` | Evaluation suites and scripts that run conversations through the graph. |