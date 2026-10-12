o# QueryPilot-AI — SQL & Document AI Agents

A portfolio adaptation exploring natural-language queries over SQLite databases and document question answering with LangChain.

## Streamlit app

The new Python interface includes:

- **SQL Chat:** ask questions about Northwind, inspect generated SQL, and view results in tables. Read-only SQLite execution blocks mutations, limits returned rows, and interrupts expensive queries. A failed query gets one correction attempt.
- **Document Chat:** upload PDF, TXT, or Markdown files, create an in-memory embedding index, and ask questions with retrieved excerpts and source citations. Follow-up questions use recent conversation context. Without uploads, use the bundled SOLID principles document.
- Separate conversation histories for each mode, configurable model, and password-style API-key input.

### Run locally

Use Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set OPENAI_API_KEY in .env, or enter it in the app sidebar.
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Select SQL Chat and ask `Which five products have the highest unit price?`. For Document Chat, click **Index documents** before asking questions. Indexing and answers use your OpenAI account and incur API charges. Questions, schema, query-generation errors, and document excerpts can be sent to OpenAI; use sample or non-sensitive data. The app supports text-based PDFs; scanned PDFs need OCR. Uploads are limited to 10 MB total and 300 text chunks.

The SQL view returns database results directly. Document answers use the top four retrieved chunks; citations identify retrieved excerpts, but answers should still be checked. Uploaded text and embeddings remain in the browser session's server memory. Clearing the conversation does not clear the document index; changing uploads replaces it.

### Streamlit Community Cloud

Connect this GitHub repository, select `main`, and set the entrypoint to `streamlit_app.py`. Configure `OPENAI_API_KEY` and optionally `OPENAI_MODEL` in the deployment's secrets settings. Do not commit `.env` or `.streamlit/secrets.toml`. The root `requirements.txt` is for the new app; the older subprojects have separate dependencies.

### Verification

```powershell
.\.venv\Scripts\python.exe -m unittest test_querypilot test_streamlit_app -v
```

Tests cover Northwind queries, blocked mutations and attachments, row limits, CTEs, query timeouts, SQL correction, document chunking, and embeddings with test doubles. Live AI answers require an API key and model access.

## Original examples

- [SQL agent](SQL-AGENTS/index2.js): natural-language questions, generated SQL, and database observations using JavaScript, TypeORM, and the bundled Northwind SQLite database.
- [Document agent](VECTORSTORE-AGENT/src/index.ts): question answering over SOLID principles using TypeScript, HNSWLib, and OpenAI embeddings.
- [Document chat](VECTORSTORE-CHAT/): Python, FastAPI, FAISS, and a streaming WebSocket chat interface.

## Run the SQL demo

Install Node.js, then run:

```powershell
cd SQL-AGENTS
npm ci
Copy-Item .env.example .env
# Set your own OPENAI_API_KEY in .env.
npm start
```

The default question is `Employees who were hired after 2005?`. Edit the `prompt` in `index2.js` to try another question. The demo prints the agent's intermediate steps, generated queries, and database observations.



