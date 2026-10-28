# QueryPilot-AI — SQL & Document AI Agents

A portfolio adaptation exploring natural-language queries over SQLite databases and document question answering with LangChain.

**Start here:** [Project architecture](ARCHITECTURE.md) explains the current
system, company isolation, source files, data flows, planned features, and known
gaps. Update it with every feature change; see [repository instructions](AGENTS.md).

## Streamlit app

The new Python interface includes:

- **SQL Chat:** ask questions about approved company PostgreSQL datasets or the public Northwind sample, inspect generated SQL, and view bounded results. Company queries enforce published table/column permissions and a restricted SELECT policy; the SQLite sample retains its read-only authorizer. A failed policy/query can receive one correction attempt.
- **Document Chat:** upload PDF, TXT, or Markdown files, create an in-memory embedding index, and ask questions with retrieved excerpts and source citations. Follow-up questions use recent conversation context. Without uploads, use the bundled SOLID principles document.
- Separate conversation histories for each mode, configurable model, and password-style API-key input.
- Auth0 Universal Login with verified-account allowlisting and session isolation.
- Persistent companies, company-scoped Admin/User memberships, company switching,
  and single-use invitations. Admins publish shared documents; Users can read them.

### Company workspaces

See [company setup](docs/company-workspaces.md) for database initialization,
registration policy, invitations, roles, and current limitations. Development
uses an ignored local SQLite application database. Production requires PostgreSQL
through `QUERYPILOT_DATABASE_URL` and a one-time `python tenancy.py` initialization.
This application database is separate from the public Northwind SQL sample.

### Company SQL connections

Admins can publish PostgreSQL sources from server-provisioned company connection
references, inspect readable schemas, and approve explicit tables/columns. SQL
Chat enforces that policy before execution and returning results. See
[data source setup](docs/data-sources.md) for credential provisioning, TLS,
supported SELECT syntax, initialization, and deployment-validation requirements.
Northwind remains available as a public sample.

### Authentication and production design

Authentication is required by default. Configure Auth0 using the
[setup guide](docs/auth0-setup.md) and
[Streamlit secrets template](.streamlit/secrets.toml.example). Auth0 can offer
email/password and social login through one hosted login page. The app does not
store passwords. Missing authentication configuration blocks workspace access.

The [architecture reference](ARCHITECTURE.md) tracks implemented authentication,
company authorization, persistence, and PostgreSQL source permissions alongside
planned ingestion, AI quotas, and operations. The full production system is not
yet complete.

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

The SQL view returns results from the selected company PostgreSQL source or public Northwind sample. Document answers use the top four retrieved chunks; citations identify retrieved excerpts, but answers should still be checked. Admin-indexed document text is persisted in the application database and shared only with that company's members. Vectors and conversations remain in session memory. Clearing a conversation does not delete published documents. Company or role changes clear session workspace state; SQL source/policy changes clear SQL history.

### Streamlit Community Cloud

Connect this GitHub repository, select `main`, and set the entrypoint to `streamlit_app.py`. Configure `GROQ_API_KEY`, optionally `GROQ_MODEL`, and the `[auth]` and `[app_auth]` tables from the Auth0 setup guide in deployment secrets. Register the deployed callback URL in Auth0. Do not commit `.env` or `.streamlit/secrets.toml`. The root `requirements.txt` is for the new app; the older subprojects have separate dependencies.

### Verification

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_data_connections test_tenancy test_querypilot test_streamlit_app test_groq test_authentication test_local_auth_server -v
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



