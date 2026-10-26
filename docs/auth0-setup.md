# Auth0 setup

QueryPilot uses Auth0 Universal Login through Streamlit's native OpenID Connect
integration. Auth0 has a Free plan; review [current pricing](https://auth0.com/pricing)
and connection availability before enabling features. This implementation does
not depend on a paid Auth0 feature. Email/password and social login are configured
in Auth0 rather than implemented as password forms in QueryPilot.

## Configure the provider

1. Create an Auth0 tenant and a **Regular Web Application**. The Streamlit server
   is a confidential client. Do not use a Single Page Application or a management
   API token for this integration.
2. Enable the database/email-password connection and any desired social
   connections for that application. For Google in production, configure your
   own Google OAuth credentials rather than Auth0's development keys. Password
   reset and email verification are handled by Auth0. Configure production email
   delivery and verify any free-plan limitations before launching.
3. Set **Allowed Callback URLs** to the exact public callback URL, for example
   `https://querypilot.example.com/oauth2callback`. Register
   `http://localhost:8501/oauth2callback` separately if testing locally.
4. Set **Allowed Logout URLs** to your app origin, for example
   `https://querypilot.example.com` (and the localhost origin when developing).
   If Auth0 asks for Allowed Web Origins, use the same app origins. Keep HTTPS in
   production. Do not register wildcard callbacks.
5. Record the tenant domain and Application Client ID. Copy the Client Secret
   directly to the deployment's secure secrets configuration; never send it in
   chat or commit it.

## Configure QueryPilot

The selected tenant is `dev-5thnlkpjf2semjay.us.auth0.com`. Its discovery URL is
`https://dev-5thnlkpjf2semjay.us.auth0.com/.well-known/openid-configuration`.
The application Client ID is `ue8XBhVxsV5BoxxXWDyeOeASOrHDKmSO`.
Initial testing uses `http://localhost:8501` with callback
`http://localhost:8501/oauth2callback`. The example files select local development
with authentication enabled. The allowed account email still needs to be supplied.
For deployment, set `APP_ENV=production` and replace the callback with the public
HTTPS URL in both Streamlit secrets and Auth0.

Copy `.streamlit/secrets.toml.example` to the ignored
`.streamlit/secrets.toml`, or use your hosting platform's Streamlit secrets
editor. Fill in the public callback, tenant discovery URL, client ID, and client
secret. Generate a strong random cookie signing secret (at least 32 characters)
directly in your secret manager. Keep it stable across app replicas and restarts.
Rotate it after a suspected compromise. Do not expose access or ID tokens.

Set `[app_auth].allowed_emails` to the verified account emails allowed to enter
the app. A missing or empty list denies everyone. This is an initial private-app
access policy; workspace memberships and roles are a later design stage. Email
changes require updating the allowlist; identity remains keyed by issuer/subject.
Set `max_session_seconds` between 300 and 86400 (default 28800, eight hours).

The native OIDC library reads these values from Streamlit secrets; environment
variables named `AUTH0_CLIENT_SECRET` alone are not read by this integration.
Client and cookie secrets must be available locally to the application. Proxy
placeholder secret bindings are not appropriate for the cookie signing secret.
Use your deployment secret manager to mount/render the ignored secrets file.

Allow server HTTPS egress to your exact Auth0 tenant or custom domain for OIDC
discovery, code exchange, and signing keys. Social-provider login happens in the
user's browser through Auth0. Final networking settings depend on the tenant;
do not add a broad wildcard domain as a substitute.

Start normally with authentication enforced:

```bash
cd /workspace/QueryPilot-AI
.venv/bin/python -m streamlit run streamlit_app.py --server.headless=true
```

For local OIDC on an HTTP localhost callback, set `APP_ENV=development` and leave
`DEV_AUTH_BYPASS=false`. Missing configuration blocks access by default.

For offline development only, bypass requires both explicit settings:

```bash
APP_ENV=development DEV_AUTH_BYPASS=true .venv/bin/python -m streamlit run streamlit_app.py
```

Never enable this bypass in a public deployment. An attempted bypass with
`APP_ENV=production` is blocked. Do not disable Streamlit CORS or XSRF protection.

## Validation and rollout

```bash
.venv/bin/python -m unittest test_querypilot test_streamlit_app test_groq test_authentication -v
.venv/bin/python -m pip check
```

Automated tests cover anonymous denial, login delegation, identity validation,
allowlist enforcement, expiry, account changes, logout cleanup, and server-key
non-disclosure, alongside the app's SQL and document workflows. Provider claims
and login redirects are mocked; these tests do not prove Auth0 callback behavior.

On the actual HTTPS origin, verify all of the following before rollout:

- Anonymous users see sign-in only, with no database schema, upload, or chat UI.
- An allowed user completes Universal Login and can access both chat modes.
- Unverified and non-allowlisted accounts are denied.
- Password reset and email verification work for database accounts.
- Logout removes workspace state; another account sees no prior chats or uploads.
- An expired identity is rejected on the next app interaction.
- The configured Groq key is never rendered into the browser's input field.

Streamlit logout is session-scoped; another already-open tab can continue until
its identity expires or it logs out. Checks run on reruns and do not interrupt
work already in progress. Organization-wide revocation, persistent memberships,
ingress rate limiting, per-user AI quotas, and production monitoring are future
work described in [production-design.md](production-design.md).
