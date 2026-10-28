"""Company-scoped PostgreSQL connections and a deliberately bounded SELECT policy.

Customer credentials are deployment-owned, never accepted as chat/UI input.
Only approved lower-case SQL identifiers and a non-nested SELECT subset are supported.
"""

from contextlib import contextmanager
import hashlib
import json
import os
import re
import time

import psycopg
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

from tenancy import AccessDenied


class DataConnectionError(ValueError):
    pass


def valid_identifier(value):
    return (isinstance(value, str) and re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', value)
            and not value.startswith('pg_') and value != 'information_schema')


def validate_permissions(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 100:
        raise ValueError('Approve between 1 and 100 tables with explicit columns.')
    result = {}
    for table, columns in value.items():
        if (not valid_identifier(table) or not isinstance(columns, list)
                or not 1 <= len(columns) <= 100 or any(not valid_identifier(c) for c in columns)
                or len(set(columns)) != len(columns)):
            raise ValueError('Use explicit lower-case table and column names; wildcards are not permissions.')
        result[table] = sorted(columns)
    return dict(sorted(result.items()))


def configured_references(company_id):
    try:
        config = json.loads(os.getenv('QUERYPILOT_DATA_CONNECTIONS', '{}'))
        company = config.get(company_id, {})
        if not isinstance(company, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                               for k, v in company.items()):
            raise ValueError()
        return company
    except (ValueError, AttributeError):
        raise DataConnectionError('Company connection configuration is invalid.') from None


def connection_url(company_id, reference):
    url = configured_references(company_id).get(reference)
    if not url:
        raise DataConnectionError('This connection reference is not assigned to this company.')
    try:
        parsed = make_url(url)
        if (parsed.drivername != 'postgresql' or not parsed.host or not parsed.database
                or not parsed.username or not parsed.password or parsed.query):
            raise ValueError()
        if (any(character in parsed.host for character in ',/\\')
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,62}', parsed.database)):
            raise ValueError()
        # Disallow embedding libpq overrides, service files, options or alternate hosts.
        app_url = os.getenv('QUERYPILOT_DATABASE_URL', '')
        if app_url:
            application = make_url(app_url)
            if (application.host, application.port or 5432, application.database) == (
                    parsed.host, parsed.port or 5432, parsed.database):
                raise ValueError()
    except (ValueError, TypeError, ArgumentError):
        raise DataConnectionError('Use a dedicated PostgreSQL customer database with a valid server-owned connection URL.') from None
    return parsed


@contextmanager
def read_connection(company_id, reference):
    parsed = connection_url(company_id, reference)
    try:
        # Individual keyword fields prevent URL-controlled libpq parameter overrides.
        with psycopg.connect(host=parsed.host, port=parsed.port or 5432,
                dbname=parsed.database, user=parsed.username, password=parsed.password,
                connect_timeout=5, sslmode='verify-full', application_name='querypilot-reader') as connection:
            connection.read_only = True
            connection.execute("SET LOCAL statement_timeout = '3s'")
            connection.execute("SET LOCAL lock_timeout = '1s'")
            connection.execute("SET LOCAL idle_in_transaction_session_timeout = '5s'")
            connection.execute('SET LOCAL search_path = pg_catalog')
            privileged = connection.execute('''SELECT rolsuper OR rolbypassrls OR rolcreaterole
                OR rolcreatedb OR rolreplication FROM pg_catalog.pg_roles WHERE rolname = current_user''').fetchone()
            if not privileged or privileged[0]:
                raise DataConnectionError('Use a dedicated restricted read-only database role.')
            writable = connection.execute('''SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                AND (pg_catalog.has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')
                OR pg_catalog.has_any_column_privilege(c.oid, 'INSERT,UPDATE'))
                )''').fetchone()
            if not writable or writable[0]:
                raise DataConnectionError('Database role has write privileges. Supply a read-only role.')
            yield connection
    except psycopg.Error:
        # Raw messages may contain connection details, SQL or hidden identifiers.
        raise DataConnectionError('Customer database operation failed. Check connectivity, read permissions, and query limits.') from None


def discover_schema(store, principal, company_id, reference, schema_name):
    store.require(principal, company_id, admin=True)
    if not valid_identifier(schema_name):
        raise ValueError('Use a supported lower-case schema name.')
    with read_connection(company_id, reference) as connection:
        rows = connection.execute('''SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = %s ORDER BY table_name, ordinal_position LIMIT 10001''',
            (schema_name,)).fetchall()
    if len(rows) > 10000:
        raise DataConnectionError('Schema is too large. Expose a smaller reporting schema.')
    result = {}
    for table, column in rows:
        if valid_identifier(table) and valid_identifier(column):
            result.setdefault(table, []).append(column)
    store.require(principal, company_id, admin=True)
    return result


def publish_source(store, principal, company_id, name, reference, schema_name,
                   permissions, source_id=None, expected_revision=None):
    store.require(principal, company_id, admin=True)
    permissions = validate_permissions(permissions)
    actual = discover_schema(store, principal, company_id, reference, schema_name)
    if any(table not in actual or not set(columns) <= set(actual[table])
           for table, columns in permissions.items()):
        raise ValueError('Approved tables/columns must exist and be readable by the connection role.')
    return store.save_data_source(principal, company_id, name, reference, schema_name,
                                 permissions, source_id, expected_revision)


# Reject unfamiliar AST nodes rather than assuming all SELECT expressions are safe.
ALLOWED_NODES = {getattr(exp, name) for name in (
    'Select', 'From', 'Table', 'Identifier', 'TableAlias', 'Column', 'Alias',
    'Literal', 'Null', 'Boolean', 'Star', 'Where', 'Group', 'Having', 'Order',
    'Ordered', 'Limit', 'Offset', 'Join', 'Distinct', 'Paren', 'And', 'Or', 'Not',
    'EQ', 'NEQ', 'GT', 'GTE', 'LT', 'LTE', 'Add', 'Sub', 'Mul', 'Div', 'Mod',
    'Neg', 'Between', 'In', 'Is', 'Like', 'ILike', 'Count', 'Sum', 'Avg', 'Min',
    'Max', 'Lower', 'Upper', 'Length', 'Coalesce', 'Abs', 'Round')}


def validate_sql(sql, schema_name, permissions):
    permissions = validate_permissions(permissions)
    if not valid_identifier(schema_name) or not isinstance(sql, str) or len(sql) > 20000:
        raise ValueError('Query or schema exceeds the supported policy.')
    try:
        statements = sqlglot.parse(sql, read='postgres')
        if len(statements) != 1 or not isinstance(statements[0], exp.Select):
            raise ValueError()
        query = statements[0]
        if sum(1 for _ in query.find_all(exp.Select)) != 1:
            raise ValueError()
        for node in query.walk():
            if type(node) not in ALLOWED_NODES:
                raise ValueError()
            # SQL comments/hints are unnecessary and removed before execution.
            node.comments = None
        for join in query.find_all(exp.Join):
            if join.args.get('method') or join.args.get('using'):
                raise ValueError()
        tables = list(query.find_all(exp.Table))
        if not tables or len(tables) > 10:
            raise ValueError()
        aliases = {}
        for table in tables:
            if (not isinstance(table.this, exp.Identifier) or table.catalog
                    or (table.db and table.db != schema_name) or table.name not in permissions
                    or table.alias_or_name in aliases or table.args.get('pivots')
                    or (table.args.get('alias') and table.args['alias'].args.get('columns'))):
                raise ValueError()
            aliases[table.alias_or_name] = table.name
            table.set('db', exp.to_identifier(schema_name))
        query = qualify(query, dialect='postgres', schema={schema_name: {
            table: {column: 'UNKNOWN' for column in columns} for table, columns in permissions.items()}},
            validate_qualify_columns=True, allow_partial_qualification=False)
        # Qualification can introduce whole-row TableColumn expressions. Recheck
        # the rewritten tree so a relation alias cannot expose its hidden fields.
        if any(type(node) not in ALLOWED_NODES for node in query.walk()):
            raise ValueError()
        for column in query.find_all(exp.Column):
            if (not column.table and column.name in query.named_selects
                    and column.find_ancestor(exp.Order) is not None):
                continue
            if (column.catalog or column.db or column.table not in aliases
                    or column.name not in permissions[aliases[column.table]]):
                raise ValueError()
        for star in query.find_all(exp.Star):
            if not isinstance(star.parent, exp.Count):
                raise ValueError()
        # Bound returned rows on the server as well as in the response.
        limit = query.args.get('limit')
        if limit:
            value = limit.expression
            if not isinstance(value, exp.Literal) or value.is_string or not value.this.isdigit():
                raise ValueError()
            if int(value.this) > 201:
                query = query.limit(201)
        else:
            query = query.limit(201)
        return query.sql(dialect='postgres')
    except (ValueError, SqlglotError, KeyError, TypeError):
        raise ValueError('Query is outside the approved SELECT/table/column policy. Use a simple SELECT with approved columns.') from None


def source_fingerprint(source):
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


class CompanySQL:
    """Authorization is checked before schema disclosure, execution and result return."""
    def __init__(self, store, principal, company_id, source_id):
        self.store, self.principal = store, principal
        self.company_id, self.source_id = company_id, source_id

    def source(self):
        return self.store.get_data_source(self.principal, self.company_id, self.source_id)

    def fingerprint(self, source=None):
        source = source if source is not None else self.source()
        binding = connection_url(self.company_id, source['secret_ref']).render_as_string(hide_password=False)
        return hashlib.sha256((source_fingerprint(source) + binding).encode()).hexdigest()

    def schema(self):
        source = self.source()
        connection_url(self.company_id, source['secret_ref'])
        return '\n'.join(f"{source['schema_name']}.{table} ({', '.join(columns)})"
                         for table, columns in source['allowed_columns'].items())

    def execute(self, sql, expected_fingerprint):
        source = self.source()
        if self.fingerprint(source) != expected_fingerprint:
            raise AccessDenied('Data source permissions changed. Start a new question.')
        safe_sql = validate_sql(sql, source['schema_name'], source['allowed_columns'])
        rows, byte_count, truncated = [], 0, False
        with read_connection(self.company_id, source['secret_ref']) as connection:
            deadline = time.monotonic() + 3
            with connection.cursor(name='querypilot_result') as cursor:
                cursor.execute(safe_sql)
                # Named cursors populate description after the first fetch.
                while True:
                    if time.monotonic() > deadline:
                        raise DataConnectionError('Query exceeded its execution deadline.')
                    row = cursor.fetchone()
                    if row is None:
                        break
                    size = len(json.dumps(row, default=str).encode())
                    if len(rows) >= 200 or byte_count + size > 1024 * 1024:
                        truncated = True
                        break
                    byte_count += size
                    rows.append(row)
                columns = [column.name for column in cursor.description]
        if self.fingerprint() != expected_fingerprint:
            raise AccessDenied('Data source permissions changed. Results were discarded.')
        return {'sql': safe_sql, 'columns': columns, 'rows': rows, 'truncated': truncated}

    def answer(self, model, question, history, expected_fingerprint=None):
        from querypilot import generate_sql
        expected = expected_fingerprint or self.fingerprint()
        if self.fingerprint() != expected:
            raise AccessDenied('Data source permissions changed. Start a new question.')
        schema = self.schema()
        error = ''
        for attempt in range(2):
            sql = generate_sql(model, question, history, schema, error, dialect='PostgreSQL')
            try:
                return self.execute(sql, expected)
            except (AccessDenied, DataConnectionError):
                raise
            except ValueError:
                # Do not send raw customer DB errors or hidden identifiers to the model.
                error = 'The query violated the supported SELECT policy. Use approved tables and columns only.'
                if attempt == 1:
                    raise ValueError('Unable to produce a query within the approved dataset policy.') from None
