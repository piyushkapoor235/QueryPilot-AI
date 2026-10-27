# Company workspaces: first implementation checkpoint

The app now persists users, companies, memberships, invitation hashes, extracted
document text, and company-change audit events. A verified principal is provided
by the existing Streamlit/Auth0 boundary. Backend methods independently check
membership and Admin permissions; hiding controls is not the security boundary.

## Run locally

Install the root requirements in your existing environment. With
`APP_ENV=development`, the app initializes `.querypilot/application.db` on first
use. This directory is ignored by Git. Back it up if you need its contents.
The existing Auth0 configuration is still required unless you explicitly enable
the existing development-only authentication bypass.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

After signing in, use **Create or join a company**. Creating a company makes you
its first Admin. Choose a company using the sidebar selector. Switching companies
or changing your company role clears chats, document indexes, and user-entered
API keys from that session. The same account may be an Admin in one company and
a User in another.

## Team access

In **Company administration**, create an invitation for an exact verified email.
The default role is User. Share the displayed code privately using your chosen
channel; the application does not send email. The code expires after 24 hours,
is usable once, and only its SHA-256 hash is stored. The recipient signs in and
enters it under **Create or join a company**. It does not change the role of an
existing member. Use the role-management form for that. The final Admin cannot
be demoted, including during concurrent role changes.

By default, `[app_auth].access_mode` remains `"allowlist"`. Every invited person
must also pass that global allowlist. For multi-company registration, explicitly
set `access_mode = "verified_users"` in Streamlit secrets. This allows verified
accounts to create their own companies; joining another company still requires
an invitation. `allowed_emails` is optional in this mode. Unknown modes fail
closed. There is no automatic company membership based on an email domain.

## Documents and SQL

Only Admins can upload and index documents. Indexing validates/builds the new
index before atomically replacing that company's published extracted text.
Members can retrieve published company documents without uploading or publishing.
When an Admin indexes without uploads, the existing published text is used; an
empty company can publish the explicitly labeled public SOLID sample.

SQL Chat now supports company-specific PostgreSQL sources with Admin-published
table/column permissions; see [data source setup](data-sources.md). Northwind
remains a separate public sample. Customer business-record editing is not
implemented. Do not replace the bundled Northwind file with private data.

## PostgreSQL deployment

Set `APP_ENV=production` and provide a private `QUERYPILOT_DATABASE_URL` using the
`postgresql+psycopg://` driver and appropriate TLS settings. Keep credentials out
of Git, screenshots, and logs. SQLite is rejected outside development.

Initialize the schema once with deployment credentials before starting the app:

```powershell
.\.venv\Scripts\python.exe tenancy.py
```

Production app startup does not create schema. Use a separate runtime database
role with only the required table permissions. The initializer creates the first
schema only; future changes need versioned migrations. It never reads or alters
the Northwind database.

Company writes serialize on a company row in PostgreSQL. Local SQLite uses
`BEGIN IMMEDIATE` to serialize writes and enable foreign keys. All resource
operations filter by company and check current membership. PostgreSQL RLS is
not implemented in this checkpoint. The current tests exercise SQLite;
PostgreSQL requires integration and concurrency validation before deployment.

## Remaining work

- Additional database connectors, per-member dataset grants, and controlled edits.
- Membership removal, invitation revocation, and account suspension.
- Object storage, parser isolation, asynchronous ingestion, and document deletion.
- Persistent chats, centralized session revocation, and stricter admin reauthentication.
- Redis quotas, token accounting, billing, and operational monitoring.
- PostgreSQL RLS, versioned migrations, production backup/restore validation.

Parsing is still synchronous and original file bytes are not persisted. Extracted
text is stored in the application database. A 10 MB text and 300-chunk limit is
enforced server-side. Session-local chat can retain excerpts previously displayed
before a document replacement; replacement is not a historical-chat purge.
Do not enable public self-service registration until quotas and abuse controls
are in place.

## Verification

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_data_connections test_tenancy test_querypilot test_streamlit_app test_groq test_authentication test_local_auth_server -v
```

Tests include cross-company denial, read-only User permissions, role changes,
last-Admin concurrency, invitation identity/expiry/replay, document persistence,
company switching, onboarding, and the existing auth/SQL/document flows. AI and
identity-provider interactions are mocked; these tests do not prove a deployed
Auth0 callback, PostgreSQL isolation, or production capacity.
