from contextlib import contextmanager
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import psycopg
from sqlalchemy import select
import sqlglot
from sqlglot import exp
from streamlit.testing.v1 import AppTest

from authentication import Principal
from data_connections import (CompanySQL, DataConnectionError, connection_url,
    discover_schema, publish_source, read_connection, validate_permissions, validate_sql)
from tenancy import AccessDenied, CompanyStore, audits
from test_company_support import DEVELOPER, company_fixture


PERMISSIONS = {'products': ['id', 'name', 'price'], 'orders': ['id', 'product_id']}
FAKE_URL = 'postgresql://report_reader:test-only-password@reports.example:5432/warehouse'


class SQLPolicyTests(unittest.TestCase):
    def test_safe_projection_star_join_aggregate_and_limits(self):
        for query in [
            'SELECT * FROM products',
            'SELECT p.* FROM products p',
            'SELECT name, price FROM products ORDER BY price DESC LIMIT 5',
            'SELECT COUNT(*) AS total FROM products',
            'SELECT p.name, SUM(o.id) AS n FROM products p JOIN orders o ON p.id=o.product_id GROUP BY p.name ORDER BY n DESC',
            'SELECT name FROM products WHERE price > 10 AND name ILIKE \'a%\' LIMIT 999999',
            'SELECT name FROM products WHERE id IN (1, 2, 3)',
            'SELECT lower(name) AS name FROM public.products',
        ]:
            with self.subTest(query=query):
                safe = validate_sql(query, 'public', PERMISSIONS)
                tree = sqlglot.parse_one(safe, read='postgres')
                self.assertLessEqual(int(tree.args['limit'].expression.this), 201)
                self.assertTrue(all(table.db == 'public' for table in tree.find_all(exp.Table)))
        safe = validate_sql('SELECT * FROM products', 'public', PERMISSIONS)
        self.assertNotIn('*', safe)
        self.assertNotIn('salary', safe)

    def test_denies_hidden_data_unsafe_functions_and_query_shapes(self):
        queries = [
            'DELETE FROM products', 'UPDATE products SET price=1',
            'SELECT * FROM products; SELECT * FROM payroll',
            'SELECT * FROM payroll', 'SELECT * FROM other.products',
            'SELECT * FROM pg_catalog.pg_authid', 'SELECT * FROM information_schema.columns',
            'SELECT salary FROM products', 'SELECT name FROM products WHERE salary > 1',
            'SELECT name FROM products ORDER BY salary', 'SELECT name FROM products GROUP BY salary',
            'SELECT name FROM products HAVING MAX(salary)>1',
            'SELECT p.name FROM products p JOIN orders o ON p.salary=o.id',
            'SELECT * FROM products NATURAL JOIN orders',
            'SELECT * FROM products JOIN orders USING (id)',
            'SELECT pg_sleep(10) FROM products', 'SELECT public.lower(name) FROM products',
            "SELECT pg_read_file('/etc/passwd') FROM products",
            "SELECT dblink('secret', 'select 1') FROM products",
            'SELECT * INTO stolen FROM products', 'SELECT * FROM products FOR UPDATE',
            'SELECT * FROM (SELECT * FROM products) x',
            'WITH x AS (SELECT * FROM products) SELECT * FROM x',
            'SELECT id FROM products UNION SELECT id FROM orders',
            'SELECT name::text FROM products', 'SELECT COUNT(*) OVER () FROM products',
            'SELECT * FROM generate_series(1,1000000)',
            'SELECT products FROM products', 'SELECT p FROM products p',
            'SELECT count(p) FROM products p', 'SELECT 1',
        ]
        for query in queries:
            with self.subTest(query=query), self.assertRaises(ValueError):
                validate_sql(query, 'public', PERMISSIONS)

    def test_permissions_are_explicit_and_bounded(self):
        for permissions in [{}, {'products': ['*']}, {'pg_authid': ['name']},
                            {'products': []}, {'products': ['name', 'name']},
                            {'products': ['Name']}, {'products': 'name'}]:
            with self.subTest(permissions=permissions), self.assertRaises(ValueError):
                validate_permissions(permissions)


class SourceTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = CompanyStore('sqlite:///' + (Path(folder.name) / 'app.db').as_posix(), initialize=True)
        self.addCleanup(self.store.engine.dispose)
        self.admin = Principal('issuer', 'admin', 'admin@example.com')
        self.user = Principal('issuer', 'user', 'user@example.com')
        self.other = Principal('issuer', 'other', 'other@example.com')
        for person in [self.admin, self.user, self.other]:
            self.store.register(person)
        self.a = self.store.create_company(self.admin, 'A')
        self.b = self.store.create_company(self.other, 'B')
        token = self.store.invite(self.admin, self.a, self.user.email)
        self.store.accept_invitation(self.user, token)
        config = patch.dict(os.environ, {'QUERYPILOT_DATA_CONNECTIONS': json.dumps({
            self.a: {'reports': FAKE_URL}, self.b: {'other-reports': FAKE_URL}}),
            'QUERYPILOT_DATABASE_URL': ''})
        config.start()
        self.addCleanup(config.stop)
        self.source = self.store.save_data_source(self.admin, self.a, 'Reports', 'reports', 'public', PERMISSIONS)
        self.backend = CompanySQL(self.store, self.user, self.a, self.source)

    def test_sources_are_company_scoped_and_admin_only(self):
        self.assertEqual(self.store.list_data_sources(self.user, self.a)[0]['id'], self.source)
        with patch('data_connections.psycopg.connect') as connect:
            for operation in [
                lambda: self.store.get_data_source(self.other, self.b, self.source),
                lambda: CompanySQL(self.store, self.other, self.b, self.source).schema(),
                lambda: self.store.get_data_source(self.other, self.a, self.source),
                lambda: self.store.save_data_source(self.user, self.a, 'Hack', 'reports', 'public', PERMISSIONS),
                lambda: self.store.delete_data_source(self.user, self.a, self.source),
                lambda: discover_schema(self.store, self.user, self.a, 'reports', 'public'),
                lambda: publish_source(self.store, self.user, self.a, 'Hack', 'reports', 'public', PERMISSIONS),
            ]:
                with self.assertRaises(AccessDenied):
                    operation()
            connect.assert_not_called()
        self.assertNotIn('password', self.backend.schema())
        self.assertNotIn('salary', self.backend.schema())

    def test_references_cannot_cross_companies_or_target_application_database(self):
        with self.assertRaises(DataConnectionError):
            connection_url(self.b, 'reports')
        with patch.dict(os.environ, {'QUERYPILOT_DATABASE_URL': FAKE_URL.replace('postgresql:', 'postgresql+psycopg:')}):
            with self.assertRaises(DataConnectionError):
                connection_url(self.a, 'reports')
        for url in ['invalid secret', FAKE_URL + '?sslmode=disable', FAKE_URL.replace('warehouse', 'a=b')]:
            with patch.dict(os.environ, {'QUERYPILOT_DATA_CONNECTIONS': json.dumps({self.a: {'reports': url}})}):
                with self.assertRaises(DataConnectionError) as error:
                    connection_url(self.a, 'reports')
                self.assertNotIn(url, str(error.exception))

    def test_publish_checks_actual_schema_and_revision(self):
        with patch('data_connections.discover_schema', return_value=PERMISSIONS):
            with self.assertRaises(ValueError):
                publish_source(self.store, self.admin, self.a, 'Reports', 'reports', 'public', {'products': ['salary']})
            publish_source(self.store, self.admin, self.a, 'Reports', 'reports', 'public', {'products': ['id']}, self.source, 1)
            with self.assertRaisesRegex(ValueError, 'Reload'):
                publish_source(self.store, self.admin, self.a, 'Reports', 'reports', 'public', PERMISSIONS, self.source, 1)
        with self.store.engine.connect() as connection:
            actions = connection.execute(select(audits.c.action).where(audits.c.company_id == self.a)).scalars().all()
        self.assertEqual(actions.count('data_source.saved'), 2)

    def test_permission_change_before_execution_never_connects(self):
        expected = self.backend.fingerprint()
        self.store.save_data_source(self.admin, self.a, 'Reports', 'reports', 'public', {'products': ['id']}, self.source, 1)
        with patch('data_connections.psycopg.connect') as connect:
            with self.assertRaises(AccessDenied):
                self.backend.execute('SELECT name FROM products', expected)
            connect.assert_not_called()

    def fake_reader(self, rows, on_fetch=None):
        cursor = MagicMock()
        cursor.description = [MagicMock(name='description')]
        cursor.description[0].name = 'name'
        def fetch():
            if on_fetch:
                on_fetch()
            return rows.pop(0) if rows else None
        cursor.fetchone.side_effect = fetch
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        @contextmanager
        def reader(*_):
            yield connection
        return reader, cursor

    def test_query_execution_expands_allowed_star_and_limits_results(self):
        reader, cursor = self.fake_reader([('value',)] * 201)
        with patch('data_connections.read_connection', reader):
            result = self.backend.execute('SELECT * FROM products', self.backend.fingerprint())
        self.assertEqual(len(result['rows']), 200)
        self.assertTrue(result['truncated'])
        self.assertNotIn('*', cursor.execute.call_args.args[0])
        self.assertEqual(result['columns'], ['name'])
        reader, _ = self.fake_reader([('x' * (1024 * 1024 + 1),)])
        with patch('data_connections.read_connection', reader):
            result = self.backend.execute('SELECT name FROM products', self.backend.fingerprint())
        self.assertEqual(result['rows'], [])
        self.assertTrue(result['truncated'])

    def test_permission_change_during_execution_discards_results(self):
        def change():
            current = self.backend.source()
            self.store.save_data_source(self.admin, self.a, 'Reports', 'reports', 'public',
                                       {'products': ['id']}, self.source, current['revision'])
        reader, _ = self.fake_reader([('private',)], on_fetch=change)
        with patch('data_connections.read_connection', reader):
            with self.assertRaises(AccessDenied):
                self.backend.execute('SELECT name FROM products', self.backend.fingerprint())

    def test_source_removal_denies_existing_backend(self):
        self.store.delete_data_source(self.admin, self.a, self.source)
        with self.assertRaises(AccessDenied):
            self.backend.schema()

    def test_read_connection_enforces_transport_transaction_and_role(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.side_effect = [(False,), (False,)]
        with patch('data_connections.psycopg.connect') as connect:
            connect.return_value.__enter__.return_value = connection
            with read_connection(self.a, 'reports') as active:
                self.assertIs(active, connection)
            self.assertEqual(connect.call_args.kwargs['sslmode'], 'verify-full')
            self.assertEqual(connect.call_args.kwargs['connect_timeout'], 5)
        self.assertIs(connection.read_only, True)
        statements = [call.args[0] for call in connection.execute.call_args_list]
        self.assertIn("SET LOCAL statement_timeout = '3s'", statements)
        for checks in [[(True,)], [(False,), (True,)]]:
            connection.execute.return_value.fetchone.side_effect = checks
            with patch('data_connections.psycopg.connect') as connect:
                connect.return_value.__enter__.return_value = connection
                with self.assertRaises(DataConnectionError):
                    with read_connection(self.a, 'reports'):
                        self.fail('Privileged/writable roles must never reach query execution')
        with patch('data_connections.psycopg.connect', side_effect=psycopg.OperationalError('private-password and host')):
            with self.assertRaises(DataConnectionError) as error:
                with read_connection(self.a, 'reports'):
                    pass
            self.assertNotIn('private-password', str(error.exception))


class DataSourceUITests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'APP_ENV': 'development', 'DEV_AUTH_BYPASS': 'true',
            'GROQ_API_KEY': '', 'QUERYPILOT_DATA_CONNECTIONS': '{}'})
        environment.start()
        self.addCleanup(environment.stop)
        self.store = company_fixture(self, [DEVELOPER])
        self.company = self.store.list_companies(DEVELOPER)[0]['id']
        self.config = patch.dict(os.environ, {'QUERYPILOT_DATA_CONNECTIONS': json.dumps({
            self.company: {'reports': FAKE_URL}})})
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_selected_source_changes_schema_history_and_executes_company_backend(self):
        source = self.store.save_data_source(DEVELOPER, self.company, 'Reports', 'reports', 'public', PERMISSIONS)
        app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key='sql_source_selector').value, source)
        app.text_input(key='groq_key').set_value('test-key').run()
        self.assertTrue(any('public.products' in code.value for code in app.code))
        with patch('data_connections.CompanySQL.answer', return_value={
                'sql': 'SELECT name FROM public.products', 'columns': ['name'],
                'rows': [('Widget',)], 'truncated': False}) as answer:
            app.chat_input[0].set_value('List products').run()
            answer.assert_called_once()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.chat_message), 2)
        self.store.save_data_source(DEVELOPER, self.company, 'Reports', 'reports', 'public', {'products': ['id']}, source, 1)
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['messages_SQL Chat'], [])
        self.assertFalse(any('price' in code.value for code in app.code))
        app.selectbox(key='sql_source_selector').set_value('sample').run()
        self.assertFalse(app.exception)
        self.assertTrue(any('CREATE TABLE' in code.value for code in app.code))

    def test_unavailable_company_source_disables_chat_without_sample_schema(self):
        self.store.save_data_source(DEVELOPER, self.company, 'Reports', 'reports', 'public', PERMISSIONS)
        with patch.dict(os.environ, {'QUERYPILOT_DATA_CONNECTIONS': '{}'}):
            app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(app.chat_input[0].disabled)
        self.assertFalse(any('CREATE TABLE' in code.value for code in app.code))
