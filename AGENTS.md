# Repository instructions

## Architecture reference

Read [ARCHITECTURE.md](ARCHITECTURE.md) before planning or implementing changes.
It is the central project map for humans and AI agents. Inspect the relevant
source as well; if documentation and code disagree, resolve and document the
actual behavior rather than assuming the document is correct.

## Keep architecture current

For every feature, fix, or refactor that changes application behavior, update
`ARCHITECTURE.md` in the same change before reporting completion. This includes
permissions, company isolation, schemas, dependencies, configuration, flows,
limits, integrations, deployment, and verification.

- Update affected sections, the last-updated date, and a concise change-log entry.
- Clearly distinguish implemented features from plans and incomplete work.
- Record checks actually performed and their limitations; never imply mocked or
  SQLite tests validate live Auth0 or PostgreSQL deployment.
- Update affected detailed guides and README links when necessary.
- Never put credentials, real invitation codes, or private company data in docs.
- For documentation-only changes, check relevant links and consistency; runtime
  tests are unnecessary unless executable behavior changes.

## Preserve security boundaries

Keep company authorization in backend operations, not only UI controls. Do not
expose application tables or Admin-write credentials to generated SQL. Treat
legacy examples as separate, unprotected demos unless explicitly secured.

Preserve unrelated local changes. Do not edit ignored secrets or application
data as part of ordinary code/documentation work.
