import copy
import os
import time
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from authentication import (AuthenticationError, policy_from_settings,
                            principal_from_claims)


def settings():
    return {
        'auth': {
            'client_id': 'test-client', 'client_secret': 'test-client-secret',
            'cookie_secret': 'test-only-cookie-secret-with-32-characters',
            'redirect_uri': 'https://querypilot.example/oauth2callback',
            'server_metadata_url': 'https://test.us.auth0.com/.well-known/openid-configuration',
        },
        'app_auth': {'allowed_emails': ['user@example.com']},
    }


def identity():
    now = time.time()
    return {'is_logged_in': True, 'iss': 'https://test.us.auth0.com/',
            'aud': 'test-client', 'sub': 'auth0|user-1', 'email': 'user@example.com',
            'email_verified': True, 'iat': now - 60, 'exp': now + 3600}


class PolicyTests(unittest.TestCase):
    def test_verified_allowed_identity_and_stable_id(self):
        policy = policy_from_settings(settings())
        first = principal_from_claims(identity(), policy)
        claims = identity()
        claims['email'] = 'USER@example.com'
        self.assertEqual(principal_from_claims(claims, policy).user_id, first.user_id)
        claims['sub'] = 'auth0|different-user'
        self.assertNotEqual(principal_from_claims(claims, policy).user_id, first.user_id)

    def test_rejects_invalid_or_unauthorized_claims(self):
        policy = policy_from_settings(settings())
        changes = [
            {'is_logged_in': False}, {'iss': 'https://attacker.example/'},
            {'aud': 'another-client'}, {'sub': ''}, {'email_verified': False},
            {'email_verified': 'true'}, {'email': 'other@example.com'},
            {'exp': time.time() - 1}, {'exp': None}, {'exp': float('nan')},
            {'iat': time.time() + 120}, {'iat': time.time() - 28801},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(AuthenticationError):
                principal_from_claims({**identity(), **change}, policy)

    def test_configuration_fails_closed(self):
        invalid = [
            {}, {'auth': {}},
            {**settings(), 'app_auth': {'allowed_emails': []}},
            {**settings(), 'app_auth': {'allowed_emails': 'user@example.com'}},
            {**settings(), 'app_auth': {'allowed_emails': ['user@example.com'],
                                      'max_session_seconds': 0}},
        ]
        for key, value in [('redirect_uri', 'http://localhost:8501/oauth2callback'),
                           ('server_metadata_url', 'http://test.us.auth0.com/.well-known/openid-configuration'),
                           ('expose_tokens', ['access']), ('cookie_secret', 'short')]:
            config = copy.deepcopy(settings())
            config['auth'][key] = value
            invalid.append(config)
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(AuthenticationError):
                policy_from_settings(config)
        local = settings()
        local['auth']['redirect_uri'] = 'http://localhost:8501/oauth2callback'
        self.assertTrue(policy_from_settings(local, 'development'))


class AuthenticationUITests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {
            'APP_ENV': 'production', 'DEV_AUTH_BYPASS': 'false', 'GROQ_API_KEY': '',
        })
        environment.start()
        self.addCleanup(environment.stop)

    def assert_workspace_blocked(self, app):
        self.assertFalse(app.exception)
        self.assertEqual(len(app.chat_input), 0)
        self.assertEqual(len(app.sidebar.radio), 0)

    def test_missing_configuration_and_production_bypass_are_blocked(self):
        with patch('authentication.st.secrets', {}):
            app = AppTest.from_file('streamlit_app.py').run()
        self.assert_workspace_blocked(app)
        self.assertTrue(app.error)
        with patch.dict(os.environ, {'DEV_AUTH_BYPASS': 'true'}):
            app = AppTest.from_file('streamlit_app.py').run()
        self.assert_workspace_blocked(app)

    def test_anonymous_login_delegates_to_streamlit(self):
        with patch('authentication.st.secrets', settings()), \
                patch('streamlit.user_info.UserInfoProxy.to_dict', return_value={}), \
                patch('authentication.st.login') as login:
            app = AppTest.from_file('streamlit_app.py').run()
            self.assert_workspace_blocked(app)
            app.button(key='auth_login').click().run()
            login.assert_called_once_with()

    def test_authorized_identity_and_account_switch_clear_session_data(self):
        claims = identity()
        with patch('authentication.st.secrets', settings()), \
                patch('streamlit.user_info.UserInfoProxy.to_dict', side_effect=lambda: claims.copy()):
            app = AppTest.from_file('streamlit_app.py').run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.sidebar.radio), 1)
            app.session_state['document_index'] = {'private': 'data'}
            app.session_state['messages_SQL Chat'] = [{'role': 'user', 'content': 'private'}]
            claims['sub'] = 'auth0|other-account'
            app.run()
            self.assertFalse(app.exception)
            self.assertNotIn('document_index', app.session_state)
            self.assertEqual(app.session_state['messages_SQL Chat'], [])

    def test_logout_clears_data_and_invokes_framework_logout(self):
        with patch('authentication.st.secrets', settings()), \
                patch('streamlit.user_info.UserInfoProxy.to_dict', return_value=identity()), \
                patch('authentication.st.logout') as logout:
            app = AppTest.from_file('streamlit_app.py').run()
            app.session_state['document_index'] = {'private': 'data'}
            app.button(key='auth_logout').click().run()
            logout.assert_called_once_with()
            self.assert_workspace_blocked(app)
            self.assertNotIn('document_index', app.session_state)

    def test_expired_identity_cannot_reuse_workspace_state(self):
        claims = identity()
        with patch('authentication.st.secrets', settings()), \
                patch('streamlit.user_info.UserInfoProxy.to_dict', side_effect=lambda: claims.copy()):
            app = AppTest.from_file('streamlit_app.py').run()
            app.session_state['document_index'] = {'private': 'data'}
            claims['exp'] = time.time() - 1
            app.run()
            self.assert_workspace_blocked(app)
            self.assertNotIn('document_index', app.session_state)
            self.assertTrue(app.error)


if __name__ == '__main__':
    unittest.main()
