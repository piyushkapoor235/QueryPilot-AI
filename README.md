# QueryPilot-AI — SQL & Document AI Agents

A portfolio adaptation exploring natural-language queries over SQLite databases and document question answering with LangChain.

## Streamlit app

The new Python interface includes:

- **SQL Chat:** ask questions about Northwind, inspect generated SQL, and view results in tables. Read-only SQLite execution blocks mutations, limits returned rows, and interrupts expensive queries. A failed query gets one correction attempt.
- **Document Chat:** upload PDF, TXT, or Markdown files, create an in-memory embedding index, and ask questions with retrieved excerpts and source citations. Follow-up questions use recent conversation context. Without uploads, use the bundled SOLID principles document.
- Separate conversation histories for each mode, configurable model, and password-style API-key input.
- Auth0 Universal Login with verified-account allowlisting and session isolation.

### Authentication and production design

Authentication is required by default. Configure Auth0 using the
[setup guide](docs/auth0-setup.md) and
[Streamlit secrets template](.streamlit/secrets.toml.example). Auth0 can offer
email/password and social login through one hosted login page. The app does not
store passwords. Missing authentication configuration blocks workspace access.

The [production design](docs/production-design.md) covers identity, future
workspace authorization, persistence, ingestion, AI quotas, and operations.
Authentication is the first implementation stage; the full production system
is not yet complete.

### Run locally

Use Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set GROQ_API_KEY in .env, or enter it in the app sidebar.
# Configure .streamlit/secrets.toml as described in docs/auth0-setup.md.
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Select SQL Chat and ask `Which five products have the highest unit price?`. For Document Chat, click **Index documents** before asking questions. Document indexing runs locally without API calls or model downloads. Answers use your Groq account; Groq offers a limited free API tier, subject to its current rate limits and availability. Questions, schema, query-generation errors, and document excerpts can be sent to Groq; use sample or non-sensitive data. The app supports text-based PDFs; scanned PDFs need OCR. Uploads are limited to 10 MB total and 300 text chunks.

Create a Groq API key at https://console.groq.com/keys and set `GROQ_API_KEY`, or enter the key in the sidebar. The default model is `llama-3.3-70b-versatile`; set `GROQ_MODEL` to another Groq model supporting tool calling if needed. No OpenAI key is needed for the Streamlit app.

Document retrieval uses local hashed word and word-pair vectors. It matches overlapping words rather than semantic meaning, so include relevant document terminology in your questions. Indexing works without a key; generating answers requires a Groq key.

The SQL view returns database results directly. Document answers use the top four retrieved chunks; citations identify retrieved excerpts, but answers should still be checked. Uploaded text and embeddings remain in the browser session's server memory. Clearing the conversation does not clear the document index; changing uploads replaces it.

### Streamlit Community Cloud

Connect this GitHub repository, select `main`, and set the entrypoint to `streamlit_app.py`. Configure `GROQ_API_KEY`, optionally `GROQ_MODEL`, and the `[auth]` and `[app_auth]` tables from the Auth0 setup guide in deployment secrets. Register the deployed callback URL in Auth0. Do not commit `.env` or `.streamlit/secrets.toml`. The root `requirements.txt` is for the new app; the older subprojects have separate dependencies.

### Verification

```powershell
.\.venv\Scripts\python.exe -m unittest test_querypilot test_streamlit_app test_groq test_authentication -v
```

Tests cover Northwind queries, blocked mutations and attachments, row limits, CTEs, query timeouts, SQL correction, document chunking, and embeddings with test doubles. Tests exercise local document retrieval, both chat modes, and Groq request/response handling with a mocked HTTP transport; live AI answers require a Groq API key and model access.

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



