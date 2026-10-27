# Company PostgreSQL data sources

This checkpoint connects SQL Chat to company-specific PostgreSQL databases.
Company Admins publish an explicit schema/table/column allowlist. All company
members, including Admins, use that same read-only dataset policy. Per-member
dataset grants and customer-record editing are not implemented.

## 1. Provision a connection on the server

The platform operator provisions credentials in deployment secrets; company
Admins never submit database URLs through the browser. Configure the environment
variable `QUERYPILOT_DATA_CONNECTIONS` as a JSON object keyed by **full company ID**
and connection reference:

```json
{
  "company-uuid": {
    "reporting": "postgresql://READER:ENCODED_PASSWORD@db.example.com:5432/customer_reporting"
  }
}
```

This is a shape example, not a working credential. Use the full company ID shown
under **Company SQL data sources**. URL-encode username/password components.
Store the real value privately, not in code, documentation, or chat. For local
development, the ignored `.env` can contain the JSON on one quoted line. In
production, inject it through the deployment secret manager.

References are resolved only within the current company's mapping. Reusing a
reference name across companies does not share credentials. The application
database stores the reference, never the credential URL. Editing or removing a
binding takes effect on subsequent operations; changed bindings invalidate the
session's SQL history/context. The same physical database must not be assigned
to different companies unless its database-level isolation is explicitly designed
and verified by the operator.

Only plain `postgresql://` URLs with explicit host, database, username, and password
are accepted. Query-string libpq overrides, multi-host/socket destinations, and
arbitrary connection options are rejected. TLS uses `sslmode=verify-full`; install
the database CA on the server using libpq's root-certificate configuration (for
example, `PGSSLROOTCERT`). No UI option disables certificate verification.

Use a dedicated **customer reporting database**, never QueryPilot's application
database. Exact application host/port/database matches are rejected, but aliases
are not proof of physical separation. Network egress restrictions and private
connectivity remain deployment responsibilities. Allow only provisioned customer
destinations at the network boundary.

## 2. Prepare a restricted PostgreSQL role

The customer database owner should provision a dedicated login that has only
`CONNECT`, reporting-schema `USAGE`, and `SELECT` on approved tables/views or
columns. Set its default transactions to read-only. Do not use an owner,
superuser, replication, role-management, database-creation, or RLS-bypass account.

Prefer dedicated reporting views when rows or sensitive fields must be restricted.
The application checks role flags and effective table/column write privileges
before using each connection, but database grants and customer-side schema design
are still required. Restrict executable functions and resource settings according
to the customer's database policy. There is no generic customer-write connection.

## 3. Initialize application metadata

Install the root requirements, including the pinned-major SQLGlot parser.
Development startup creates missing tables automatically. For an existing
production application database, run the additive schema initializer before
deploying this checkpoint:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tenancy.py
```

This adds `company_data_sources` without replacing existing company data. The
initializer is not a versioned migration framework. Use deployment credentials
for schema creation and grant the runtime role only necessary table permissions.

## 4. Publish permissions as a Company Admin

Open **Company SQL data sources**:

1. Select **New source**, enter a display name, and choose a provisioned reference.
2. Enter the reporting schema. The initial connector supports lower-case ASCII
   SQL identifiers only: letters/underscores followed by letters/digits/underscores,
   up to 63 characters. System schemas/names are excluded.
3. Click **Inspect readable schema** to see columns visible to the database role.
4. Enter explicit permissions as JSON, for example:

   ```json
   {"products": ["id", "name", "price"], "orders": ["id", "product_id"]}
   ```

5. Click **Test and publish permissions**. The backend checks Admin access, tests
   the connection, confirms readable columns, and saves a new policy revision.

At most 100 tables and 100 columns per table may be published. Wildcard permission
entries are rejected. Choose an existing source to edit its policy. Stale revision
updates are rejected. Removal requires a confirmation checkbox and deletes only
QueryPilot's connection metadata, not customer data. Saves/removals are audited.

## 5. Query the company source

Choose the company source under **SQL data source**. New sessions prefer a
configured company source; the public Northwind sample remains an explicit option.
Only the published column names are shown in the schema or supplied to the model.
Switching source or changing the permission/connection fingerprint clears SQL
conversation history. An unavailable selected connection disables chat and does
not silently execute its question against Northwind.

The backend enforces a deliberately limited PostgreSQL SELECT subset:

- One SELECT over approved schema-qualified tables, with ordinary joins using
  `ON`, filters, grouping, ordering, literals, and basic arithmetic/comparisons.
- Built-in `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `LOWER`, `UPPER`, `LENGTH`,
  `COALESCE`, `ABS`, and `ROUND`.
- `*` expands to explicit approved columns before execution; `COUNT(*)` is allowed.
- Hidden columns are rejected in projections, filters, join conditions, grouping,
  having, and ordering. Whole-row/composite references are rejected as well.
- CTEs, subqueries, UNION, casts, window functions, NATURAL/USING joins, custom or
  schema-qualified functions, locks, writes, and unfamiliar syntax are rejected.

SQLGlot parses/qualifies the query against the approved schema, then the rewritten
tree is checked again. The executed statement is the validated rewrite, not the
original model text. At most one policy-correction attempt is made. Raw database
errors and credential details are never sent back to the model.

Connections use a read-only transaction, `pg_catalog` search path, a five-second
connection timeout, a three-second statement timeout, a one-second lock timeout,
and an idle-transaction timeout. A server cursor returns at most 200 rows with a
one-MiB row-data response budget; a 201st row is used to detect truncation.
Elapsed time is checked between fetches. The response-byte budget is not a hard
parser/process memory or network-byte limit: a single database value is decoded
before its size is checked. Physical worker isolation remains future work.

Membership and source policy are rechecked before execution and before returning
results. A policy revision, connection binding change, or removal during the
request causes results to be discarded. This does not interrupt an already-running
database statement instantly. Previously displayed data cannot be recalled from
a user's browser.

## Validation and deployment limits

```powershell
.\.venv\Scripts\python.exe -B -m unittest test_data_connections test_tenancy test_querypilot test_streamlit_app test_groq test_authentication test_local_auth_server -v
```

Tests cover application authorization on SQLite, hostile SQL policy cases,
rewritten projections, mocked PostgreSQL transport/role checks, result truncation,
mid-request policy changes, and Streamlit source switching. No PostgreSQL server
was available during implementation. Live PostgreSQL behavior, TLS/CA setup,
column-grant discovery, named cursors, deadlines, concurrency, and customer RLS
must be integration-tested on an isolated reporting database before production.

Redis admission controls, durable token/cost accounting, managed credential rotation,
per-member dataset permissions, and process-level SQL isolation remain planned.
