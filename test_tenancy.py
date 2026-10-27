import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import select, update
from streamlit.testing.v1 import AppTest

from authentication import Principal, policy_from_settings, principal_from_claims
from tenancy import (AccessDenied, CompanyStore, audits, clear_company_state,
                     database_url, invitations)
from test_authentication import identity, settings
from test_company_support import DEVELOPER, company_fixture


class CompanyTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.url = 'sqlite:///' + (Path(folder.name) / 'app.db').as_posix()
        self.store = CompanyStore(self.url, initialize=True)
        self.addCleanup(self.store.engine.dispose)
        self.alice = Principal('issuer', 'alice', 'alice@example.com')
        self.bob = Principal('issuer', 'bob', 'bob@example.com')
        self.eve = Principal('issuer', 'eve', 'eve@example.com')
        for person in [self.alice, self.bob, self.eve]:
            self.store.register(person)
        self.a = self.store.create_company(self.alice, 'Company A')
        self.b = self.store.create_company(self.eve, 'Company B')

    def join(self, role='user'):
        token = self.store.invite(self.alice, self.a, self.bob.email, role)
        return self.store.accept_invitation(self.bob, token)

    def test_company_isolation_and_persistence(self):
        self.store.replace_documents(self.alice, self.a, [('private', 'A secret')])
        self.store.replace_documents(self.eve, self.b, [('private', 'B secret')])
        self.assertEqual(self.store.read_documents(self.alice, self.a), [('private', 'A secret')])
        for operation in [lambda: self.store.require(self.alice, self.b),
                          lambda: self.store.read_documents(self.alice, self.b),
                          lambda: self.store.replace_documents(self.alice, self.b, []),
                          lambda: self.store.list_members(self.alice, self.b),
                          lambda: self.store.invite(self.alice, self.b, self.bob.email),
                          lambda: self.store.set_role(self.alice, self.b, self.eve.user_id, 'user')]:
            with self.assertRaises(AccessDenied):
                operation()
        other = CompanyStore(self.url)
        self.addCleanup(other.engine.dispose)
        self.assertEqual(other.list_companies(self.alice)[0]['id'], self.a)
        self.assertEqual(other.read_documents(self.eve, self.b), [('private', 'B secret')])

    def test_user_can_read_but_not_administer_or_promote_self(self):
        self.join()
        self.assertEqual(self.store.require(self.bob, self.a), 'user')
        self.store.replace_documents(self.alice, self.a, [('shared', 'Hello')])
        self.assertEqual(self.store.read_documents(self.bob, self.a), [('shared', 'Hello')])
        for operation in [lambda: self.store.replace_documents(self.bob, self.a, []),
                          lambda: self.store.invite(self.bob, self.a, self.eve.email, 'admin'),
                          lambda: self.store.set_role(self.bob, self.a, self.bob.user_id, 'admin')]:
            with self.assertRaises(AccessDenied):
                operation()

    def test_roles_are_per_company_and_changes_apply_immediately(self):
        self.join()
        own = self.store.create_company(self.bob, 'Bobs company')
        self.assertEqual(self.store.require(self.bob, own), 'admin')
        self.assertEqual(self.store.require(self.bob, self.a), 'user')
        self.store.set_role(self.alice, self.a, self.bob.user_id, 'admin')
        self.store.set_role(self.alice, self.a, self.bob.user_id, 'user')
        with self.assertRaises(AccessDenied):
            self.store.require(self.bob, self.a, admin=True)

    def test_last_admin_guard_and_concurrent_demotions(self):
        with self.assertRaisesRegex(ValueError, 'at least one'):
            self.store.set_role(self.alice, self.a, self.alice.user_id, 'user')
        self.join(role='admin')

        def demote(actor):
            try:
                self.store.set_role(actor, self.a, actor.user_id, 'user')
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(demote, [self.alice, self.bob]))
        self.assertEqual(sum(results), 1)

    def test_invitation_identity_expiry_replay_and_existing_role(self):
        token = self.store.invite(self.alice, self.a, self.bob.email)
        with self.assertRaises(AccessDenied):
            self.store.accept_invitation(self.eve, token)
        self.store.accept_invitation(self.bob, token)
        with self.assertRaises(AccessDenied):
            self.store.accept_invitation(self.bob, token)
        token = self.store.invite(self.alice, self.a, self.bob.email, 'admin')
        self.store.accept_invitation(self.bob, token)
        self.assertEqual(self.store.require(self.bob, self.a), 'user')
        token = self.store.invite(self.alice, self.a, self.eve.email)
        with self.store.transaction() as connection:
            connection.execute(update(invitations).values(expires_at=0))
        with self.assertRaises(AccessDenied):
            self.store.accept_invitation(self.eve, token)

    def test_concurrent_invitation_acceptance_is_single_use(self):
        token = self.store.invite(self.alice, self.a, self.bob.email)
        def accept(_):
            try:
                self.store.accept_invitation(self.bob, token)
                return True
            except AccessDenied:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(accept, range(2))), 1)

    def test_failed_write_keeps_documents_and_success_has_audit(self):
        self.store.replace_documents(self.alice, self.a, [('v1', 'original')])
        with self.assertRaises(ValueError):
            self.store.replace_documents(self.alice, self.a, [('too big', 'x' * 400000)])
        self.assertEqual(self.store.read_documents(self.alice, self.a), [('v1', 'original')])
        with self.store.engine.connect() as connection:
            events = connection.execute(select(audits).where(audits.c.company_id == self.a)).mappings().all()
        self.assertEqual([event['action'] for event in events], ['company.created', 'documents.replaced'])

    def test_company_change_drops_keys_history_and_indexes(self):
        state = {'_auth_principal': 'identity', 'company_selector': self.b,
                 '_company_boundary': (self.a, 'admin'), 'groq_key': 'secret',
                 'document_index': 'private', 'messages_SQL Chat': ['private']}
        clear_company_state(state, self.b, 'user')
        self.assertEqual(set(state), {'_auth_principal', 'company_selector', '_company_boundary'})
        state['groq_key'] = 'another secret'
        clear_company_state(state, self.b, 'admin')
        self.assertNotIn('groq_key', state)

    def test_production_database_configuration_fails_closed(self):
        for url in ['', 'sqlite:///local.db']:
            with patch.dict(os.environ, {'APP_ENV': 'production', 'QUERYPILOT_DATABASE_URL': url}):
                with self.assertRaises(ValueError):
                    database_url()

    def test_saas_auth_is_explicit_and_still_requires_verified_email(self):
        config = settings()
        config['app_auth'] = {'access_mode': 'verified_users'}
        policy = policy_from_settings(config)
        claims = {**identity(), 'email': 'new-company@example.com'}
        self.assertEqual(principal_from_claims(claims, policy).email, claims['email'])
        with self.assertRaises(ValueError):
            principal_from_claims({**claims, 'email_verified': False}, policy)
        config['app_auth']['access_mode'] = 'typo'
        with self.assertRaises(ValueError):
            policy_from_settings(config)


class CompanyUITests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'APP_ENV': 'development',
            'DEV_AUTH_BYPASS': 'true', 'GROQ_API_KEY': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.store = company_fixture(self, [DEVELOPER])
        self.first = self.store.list_companies(DEVELOPER)[0]['id']

    def test_switch_company_clears_private_state(self):
        second = self.store.create_company(DEVELOPER, 'Second company')
        app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        app.selectbox(key='company_selector').set_value(self.first).run()
        app.text_input(key='groq_key').set_value('private-key').run()
        app.session_state['messages_SQL Chat'] = [{'role': 'user', 'content': 'private question'}]
        app.session_state['document_index'] = {'private': True}
        app.selectbox(key='company_selector').set_value(second).run()
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key='groq_key').value, '')
        self.assertEqual(app.session_state['messages_SQL Chat'], [])
        self.assertNotIn('document_index', app.session_state)

    def test_user_has_no_upload_or_admin_controls_but_reads_published_docs(self):
        owner = Principal('issuer', 'owner', 'owner@example.com')
        self.store.register(owner)
        token = self.store.invite(DEVELOPER, self.first, owner.email, 'admin')
        self.store.accept_invitation(owner, token)
        self.store.replace_documents(owner, self.first, [('policy', 'Company policy is private.')])
        app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        app.session_state['document_index'] = {'private': True}
        self.store.set_role(owner, self.first, DEVELOPER.user_id, 'user')
        app.run()
        app.sidebar.radio[0].set_value('Document Chat').run()
        self.assertFalse(app.exception)
        self.assertFalse([button for button in app.button if button.key == 'index_documents'])
        self.assertFalse([item for item in app.text_input if item.label == 'Invite email'])
        self.assertEqual(app.session_state['document_index']['chunks'][0]['source'], 'policy')

    def test_new_account_onboarding_blocks_chat_and_can_create_company(self):
        newcomer = Principal('local-development', 'newcomer', 'new@example.com')
        with patch('authentication.require_authentication', return_value=newcomer):
            app = AppTest.from_file('streamlit_app.py').run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(len(app.chat_input), 0)
            company_name = next(item for item in app.text_input if item.label == 'Company name')
            company_name.set_value('New company')
            next(button for button in app.button if button.label == 'Create company').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.chat_input), 1)
            self.assertEqual(self.store.list_companies(newcomer)[0]['role'], 'admin')
