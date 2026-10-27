"""Persistent company boundaries. Call only with a server-authenticated Principal."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import uuid

from sqlalchemy import (CheckConstraint, Column, ForeignKey, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, create_engine,
                        delete, event, insert, select, update)
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


class AccessDenied(ValueError):
    pass


metadata = MetaData()
users = Table('users', metadata,
    Column('id', String(64), primary_key=True),
    Column('issuer', Text, nullable=False), Column('subject', Text, nullable=False),
    Column('email', Text, nullable=False),
    UniqueConstraint('issuer', 'subject'))
companies = Table('companies', metadata,
    Column('id', String(36), primary_key=True), Column('name', String(100), nullable=False),
    Column('revision', Integer, nullable=False, default=0))
memberships = Table('memberships', metadata,
    Column('company_id', ForeignKey('companies.id'), primary_key=True),
    Column('user_id', ForeignKey('users.id'), primary_key=True),
    Column('role', String(5), nullable=False),
    CheckConstraint("role IN ('admin', 'user')"))
invitations = Table('invitations', metadata,
    Column('token_hash', String(64), primary_key=True),
    Column('company_id', ForeignKey('companies.id'), nullable=False),
    Column('email', Text, nullable=False), Column('role', String(5), nullable=False),
    Column('expires_at', Integer, nullable=False),
    CheckConstraint("role IN ('admin', 'user')"))
documents = Table('company_documents', metadata,
    Column('company_id', ForeignKey('companies.id'), primary_key=True),
    Column('position', Integer, primary_key=True),
    Column('source', Text, nullable=False), Column('text', Text, nullable=False))
audits = Table('company_audit_events', metadata,
    Column('id', String(36), primary_key=True),
    Column('company_id', ForeignKey('companies.id'), nullable=False),
    Column('actor_id', ForeignKey('users.id'), nullable=False),
    Column('action', Text, nullable=False), Column('target', Text, nullable=False),
    Column('created_at', Integer, nullable=False))
data_sources = Table('company_data_sources', metadata,
    Column('company_id', ForeignKey('companies.id'), primary_key=True),
    Column('id', String(36), primary_key=True),
    Column('name', String(100), nullable=False),
    Column('secret_ref', String(100), nullable=False),
    Column('schema_name', String(63), nullable=False),
    Column('allowed_columns', Text, nullable=False),
    Column('revision', Integer, nullable=False),
    UniqueConstraint('company_id', 'name'))


def database_url():
    url = os.getenv('QUERYPILOT_DATABASE_URL', '')
    production = os.getenv('APP_ENV', 'production') != 'development'
    if not url:
        if production:
            raise ValueError('Configure QUERYPILOT_DATABASE_URL with PostgreSQL in production.')
        folder = Path(__file__).parent / '.querypilot'
        folder.mkdir(exist_ok=True)
        url = 'sqlite:///' + (folder / 'application.db').as_posix()
    parsed = make_url(url)
    if production and parsed.drivername != 'postgresql+psycopg':
        raise ValueError('Production requires a postgresql+psycopg database URL.')
    return url


class CompanyStore:
    def __init__(self, url, initialize=False):
        if make_url(url).get_backend_name() not in {'sqlite', 'postgresql'}:
            raise ValueError('Use SQLite locally or PostgreSQL in production.')
        self.engine = create_engine(url, pool_pre_ping=True)
        if self.engine.dialect.name == 'sqlite':
            @event.listens_for(self.engine, 'connect')
            def sqlite_settings(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA busy_timeout=10000')
        if initialize:
            metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self.engine.connect() as connection:
            if self.engine.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                connection.begin()
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def register(self, principal):
        statement = pg_insert(users) if self.engine.dialect.name == 'postgresql' else sqlite_insert(users)
        statement = statement.values(id=principal.user_id, issuer=principal.issuer,
                                     subject=principal.subject, email=principal.email)
        with self.transaction() as connection:
            connection.execute(statement.on_conflict_do_update(
                index_elements=[users.c.id], set_={'email': principal.email}))

    def _require(self, connection, principal, company_id, admin=False, lock=False):
        # All mutating company operations serialize on this row (including role checks).
        company = select(companies).where(companies.c.id == company_id)
        if lock:
            company = company.with_for_update()
        if connection.execute(company).first() is None:
            raise AccessDenied('Company access denied.')
        role = connection.execute(select(memberships.c.role).where(
            memberships.c.company_id == company_id,
            memberships.c.user_id == principal.user_id)).scalar_one_or_none()
        if role is None or (admin and role != 'admin'):
            raise AccessDenied('Company access denied.')
        return role

    def require(self, principal, company_id, admin=False):
        with self.engine.connect() as connection:
            return self._require(connection, principal, company_id, admin)

    def list_companies(self, principal):
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(
                companies.c.id, companies.c.name, memberships.c.role).join(memberships).where(
                memberships.c.user_id == principal.user_id).order_by(companies.c.name, companies.c.id)).mappings()]

    def _audit(self, connection, principal, company_id, action, target=''):
        connection.execute(insert(audits).values(id=str(uuid.uuid4()), company_id=company_id,
            actor_id=principal.user_id, action=action, target=target, created_at=int(time.time())))

    def create_company(self, principal, name):
        name = name.strip()
        if not 1 <= len(name) <= 100:
            raise ValueError('Company name must contain 1 to 100 characters.')
        company_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(insert(companies).values(id=company_id, name=name, revision=0))
            connection.execute(insert(memberships).values(company_id=company_id,
                user_id=principal.user_id, role='admin'))
            self._audit(connection, principal, company_id, 'company.created')
        return company_id

    def list_members(self, principal, company_id):
        with self.engine.connect() as connection:
            self._require(connection, principal, company_id, admin=True)
            return [dict(row) for row in connection.execute(select(
                users.c.id, users.c.email, memberships.c.role).join(memberships).where(
                memberships.c.company_id == company_id).order_by(users.c.email)).mappings()]

    def set_role(self, principal, company_id, user_id, role):
        if role not in {'admin', 'user'}:
            raise ValueError('Invalid role.')
        with self.transaction() as connection:
            self._require(connection, principal, company_id, admin=True, lock=True)
            existing = connection.execute(select(memberships.c.role).where(
                memberships.c.company_id == company_id, memberships.c.user_id == user_id)).scalar_one_or_none()
            if existing is None:
                raise AccessDenied('Member access denied.')
            admins = connection.execute(select(memberships.c.user_id).where(
                memberships.c.company_id == company_id, memberships.c.role == 'admin')).all()
            if existing == 'admin' and role != 'admin' and len(admins) == 1:
                raise ValueError('A company must keep at least one Admin.')
            connection.execute(update(memberships).where(memberships.c.company_id == company_id,
                memberships.c.user_id == user_id).values(role=role))
            self._audit(connection, principal, company_id, 'membership.role.' + role, user_id)

    def invite(self, principal, company_id, email, role='user'):
        email = email.strip().casefold()
        if role not in {'admin', 'user'} or '@' not in email or len(email) > 320:
            raise ValueError('Enter a valid email and role.')
        token = secrets.token_urlsafe(32)
        with self.transaction() as connection:
            self._require(connection, principal, company_id, admin=True, lock=True)
            connection.execute(insert(invitations).values(
                token_hash=hashlib.sha256(token.encode()).hexdigest(), company_id=company_id,
                email=email, role=role, expires_at=int(time.time()) + 86400))
            self._audit(connection, principal, company_id, 'invitation.created', email)
        return token

    def accept_invitation(self, principal, token):
        digest = hashlib.sha256(token.strip().encode()).hexdigest()
        with self.transaction() as connection:
            invitation = connection.execute(select(invitations).where(
                invitations.c.token_hash == digest)).mappings().first()
            if invitation is None:
                raise AccessDenied('Invitation is invalid or expired.')
            # Lock company first, then re-read invitation to prevent concurrent consumption.
            connection.execute(select(companies.c.id).where(
                companies.c.id == invitation['company_id']).with_for_update()).first()
            invitation = connection.execute(select(invitations).where(
                invitations.c.token_hash == digest)).mappings().first()
            if (invitation is None or invitation['expires_at'] <= time.time()
                    or invitation['email'] != principal.email.strip().casefold()):
                raise AccessDenied('Invitation is invalid or expired.')
            company_id = invitation['company_id']
            existing = connection.execute(select(memberships.c.role).where(
                memberships.c.company_id == company_id,
                memberships.c.user_id == principal.user_id)).scalar_one_or_none()
            # Invitations never change an existing member's role.
            if existing is None:
                connection.execute(insert(memberships).values(company_id=company_id,
                    user_id=principal.user_id, role=invitation['role']))
            connection.execute(delete(invitations).where(invitations.c.token_hash == digest))
            self._audit(connection, principal, company_id, 'invitation.accepted')
        return company_id

    def read_documents(self, principal, company_id):
        with self.engine.connect() as connection:
            self._require(connection, principal, company_id)
            return [(row.source, row.text) for row in connection.execute(select(
                documents.c.source, documents.c.text).where(
                documents.c.company_id == company_id).order_by(documents.c.position))]

    def list_data_sources(self, principal, company_id):
        with self.engine.connect() as connection:
            self._require(connection, principal, company_id)
            return [dict(row) for row in connection.execute(select(
                data_sources.c.id, data_sources.c.name, data_sources.c.revision).where(
                data_sources.c.company_id == company_id).order_by(data_sources.c.name)).mappings()]

    def get_data_source(self, principal, company_id, source_id, admin=False):
        with self.engine.connect() as connection:
            self._require(connection, principal, company_id, admin=admin)
            row = connection.execute(select(data_sources).where(
                data_sources.c.company_id == company_id, data_sources.c.id == source_id)).mappings().first()
            if row is None:
                raise AccessDenied('Data source access denied.')
            result = dict(row)
            result['allowed_columns'] = json.loads(result['allowed_columns'])
            return result

    def save_data_source(self, principal, company_id, name, secret_ref, schema_name,
                         allowed_columns, source_id=None, expected_revision=None):
        from data_connections import validate_permissions, valid_identifier
        allowed_columns = validate_permissions(allowed_columns)
        name = name.strip()
        if (not 1 <= len(name) <= 100 or not 1 <= len(secret_ref) <= 100
                or not valid_identifier(schema_name)):
            raise ValueError('Enter a name, connection reference, and supported schema.')
        # References are resolved server-side per company; no credentials are stored here.
        from data_connections import connection_url
        self.require(principal, company_id, admin=True)
        connection_url(company_id, secret_ref)
        with self.transaction() as connection:
            self._require(connection, principal, company_id, admin=True, lock=True)
            if source_id:
                current = connection.execute(select(data_sources).where(
                    data_sources.c.company_id == company_id,
                    data_sources.c.id == source_id)).mappings().first()
                if current is None:
                    raise AccessDenied('Data source access denied.')
                if current['revision'] != expected_revision:
                    raise ValueError('Data source changed. Reload before saving.')
                connection.execute(update(data_sources).where(
                    data_sources.c.company_id == company_id, data_sources.c.id == source_id).values(
                    name=name, secret_ref=secret_ref, schema_name=schema_name,
                    allowed_columns=json.dumps(allowed_columns, sort_keys=True),
                    revision=current['revision'] + 1))
            else:
                source_id = str(uuid.uuid4())
                connection.execute(insert(data_sources).values(company_id=company_id, id=source_id,
                    name=name, secret_ref=secret_ref, schema_name=schema_name,
                    allowed_columns=json.dumps(allowed_columns, sort_keys=True), revision=1))
            self._audit(connection, principal, company_id, 'data_source.saved', source_id)
        return source_id

    def delete_data_source(self, principal, company_id, source_id):
        with self.transaction() as connection:
            self._require(connection, principal, company_id, admin=True, lock=True)
            result = connection.execute(delete(data_sources).where(
                data_sources.c.company_id == company_id, data_sources.c.id == source_id))
            if result.rowcount != 1:
                raise AccessDenied('Data source access denied.')
            self._audit(connection, principal, company_id, 'data_source.deleted', source_id)

    def replace_documents(self, principal, company_id, sources):
        from querypilot import chunk_text
        sources = list(sources)
        if (sum(len(text.encode('utf-8')) for _, text in sources) > 10 * 1024 * 1024
                or sum(len(chunk_text(text)) for _, text in sources) > 300
                or any(not name or len(name) > 500 for name, _ in sources)):
            raise ValueError('Documents exceed publication limits.')
        with self.transaction() as connection:
            self._require(connection, principal, company_id, admin=True, lock=True)
            connection.execute(delete(documents).where(documents.c.company_id == company_id))
            if sources:
                connection.execute(insert(documents), [dict(company_id=company_id, position=i,
                    source=name, text=text) for i, (name, text) in enumerate(sources)])
            connection.execute(update(companies).where(companies.c.id == company_id).values(
                revision=companies.c.revision + 1))
            self._audit(connection, principal, company_id, 'documents.replaced')


def clear_company_state(state, company_id, role):
    """Drop all workspace/UI inputs on a company or permission change."""
    boundary = (company_id, role)
    if state.get('_company_boundary') != boundary:
        for key in list(state):
            if key not in {'_auth_principal', 'company_selector', 'auth_login', 'auth_logout'}:
                del state[key]
        state['_company_boundary'] = boundary


if __name__ == '__main__':
    # Run once before deployment. Future schema changes require versioned migrations.
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
    store = CompanyStore(database_url(), initialize=True)
    store.engine.dispose()
    print('Company schema initialized.')
