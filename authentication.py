"""Application access policy on top of Streamlit's verified OIDC identities."""

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import streamlit as st


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class AuthPolicy:
    issuer: str
    client_id: str
    allowed_emails: frozenset[str]
    max_session_seconds: int = 28800


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    email: str

    @property
    def user_id(self):
        identity = json.dumps([self.issuer, self.subject], separators=(",", ":"))
        return hashlib.sha256(identity.encode()).hexdigest()


def policy_from_settings(settings, environment="production"):
    """Validate local settings without making discovery or token requests."""
    auth = settings.get("auth", {})
    access = settings.get("app_auth", {})
    for key in ("client_id", "client_secret", "cookie_secret", "redirect_uri",
                "server_metadata_url"):
        if not isinstance(auth.get(key), str) or not auth[key].strip():
            raise AuthenticationError("Authentication configuration is incomplete.")
    if len(auth["cookie_secret"]) < 32 or auth.get("expose_tokens"):
        raise AuthenticationError("Use a strong cookie secret and keep tokens private.")
    metadata = urlsplit(auth["server_metadata_url"])
    if (metadata.scheme != "https" or not metadata.hostname or metadata.username
            or metadata.password or metadata.query or metadata.fragment
            or metadata.path != "/.well-known/openid-configuration"):
        raise AuthenticationError("Use your Auth0 HTTPS discovery URL.")
    redirect = urlsplit(auth["redirect_uri"])
    local_redirect = (environment == "development" and redirect.scheme == "http"
                      and redirect.hostname in {"localhost", "127.0.0.1"})
    if (not redirect.hostname or redirect.username or redirect.password
            or redirect.query or redirect.fragment or redirect.path != "/oauth2callback"
            or (redirect.scheme != "https" and not local_redirect)):
        raise AuthenticationError("Configure the exact HTTPS authentication callback URL.")
    emails = access.get("allowed_emails", [])
    if (not isinstance(emails, (list, tuple)) or not emails
            or any(not isinstance(email, str) or "@" not in email for email in emails)):
        raise AuthenticationError("Configure the application account allowlist.")
    max_age = access.get("max_session_seconds", 28800)
    if type(max_age) is not int or not 300 <= max_age <= 86400:
        raise AuthenticationError("Session lifetime must be between 300 and 86400 seconds.")
    return AuthPolicy(f"https://{metadata.netloc}/", auth["client_id"],
                      frozenset(email.strip().casefold() for email in emails), max_age)


def principal_from_claims(claims, policy, now=None):
    """Apply policy only to claims already verified by Streamlit/Authlib."""
    now = time.time() if now is None else now
    if claims.get("is_logged_in") is not True or claims.get("iss") != policy.issuer:
        raise AuthenticationError("Sign in with the configured identity provider.")
    subject, email = claims.get("sub"), claims.get("email")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if policy.client_id not in audiences:
        raise AuthenticationError("The identity is not issued for this application.")
    if (not isinstance(subject, str) or not subject.strip()
            or not isinstance(email, str) or claims.get("email_verified") is not True
            or email.strip().casefold() not in policy.allowed_emails):
        raise AuthenticationError("This account is not authorized. Verify your email or contact the administrator.")
    issued, expires = claims.get("iat"), claims.get("exp")
    if (any(type(value) not in (int, float) or not math.isfinite(value)
            for value in (issued, expires))
            or issued > now + 60 or expires <= now or expires <= issued
            or now >= issued + policy.max_session_seconds):
        raise AuthenticationError("Your session has expired. Please sign in again.")
    return Principal(policy.issuer, subject, email.strip().casefold())


def clear_session(preserve_auth_controls=False):
    # Preserve a clicked login/logout control until Streamlit handles this rerun.
    # Every workspace and user-input key is still discarded.
    for key in list(st.session_state):
        if preserve_auth_controls and key in {"auth_login", "auth_logout"}:
            continue
        del st.session_state[key]


def require_authentication():
    """Stop the script before any workspace operation unless access is granted."""
    environment = os.getenv("APP_ENV", "production")
    bypass = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"
    if bypass and environment == "development":
        principal = Principal("local-development", "developer", "developer@localhost")
        st.warning("Development mode: authentication is bypassed.")
    else:
        try:
            if bypass:
                raise AuthenticationError("Authentication bypass is forbidden outside development.")
            policy = policy_from_settings(st.secrets, environment)
        except (FileNotFoundError, AuthenticationError):
            clear_session()
            st.error("Sign-in is unavailable. Ask the administrator to configure authentication.")
            st.stop()

        claims = st.user.to_dict()
        if not claims.get("is_logged_in"):
            clear_session(preserve_auth_controls=True)
            st.info("Sign in to access your QueryPilot workspace.")
            if st.button("Sign in", key="auth_login", type="primary"):
                st.login()
            st.stop()
        try:
            principal = principal_from_claims(claims, policy)
        except AuthenticationError as exc:
            clear_session(preserve_auth_controls=True)
            st.error(str(exc))
            if st.button("Sign out", key="auth_logout"):
                st.logout()
            st.stop()

    # Include the bypass principal so enabling real authentication drops local data.
    if st.session_state.get("_auth_principal") != principal.user_id:
        clear_session()
        st.session_state["_auth_principal"] = principal.user_id
    if not bypass:
        with st.sidebar:
            st.caption(f"Signed in as {principal.email}")
            if st.button("Sign out", key="auth_logout"):
                clear_session()
                st.logout()
                st.stop()
    return principal
