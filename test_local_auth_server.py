import unittest

from local_auth_server import AuthDiagnostics, has_identity_cookie


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_diagnostics_never_log_credentials(self):
        private = b'private-test-credential'
        messages = []

        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 302,
                        'headers': [(b'set-cookie', b'_streamlit_user=' + private),
                                    (b'location', b'/?code=' + private)]})

        async def send(message):
            messages.append(message)

        with self.assertLogs('querypilot.local_auth', level='WARNING') as logs:
            await AuthDiagnostics(app)(
                {'type': 'http', 'path': '/oauth2callback',
                 'query_string': b'code=' + private, 'headers': []}, None, send)
        output = '\n'.join(logs.output)
        self.assertNotIn(private.decode(), output)
        self.assertIn('sets_identity_cookie=True', output)
        self.assertEqual(messages[0]['headers'][0][1], b'_streamlit_user=' + private)

    def test_cookie_presence_does_not_authenticate(self):
        self.assertTrue(has_identity_cookie([(b'cookie', b'_streamlit_user=untrusted')]))
        self.assertFalse(has_identity_cookie([]))


if __name__ == '__main__':
    unittest.main()
