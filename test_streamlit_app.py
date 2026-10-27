import unittest
import os
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from test_company_support import company_fixture, DEVELOPER


class StreamlitTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'APP_ENV': 'development', 'DEV_AUTH_BYPASS': 'true', 'GROQ_API_KEY': '',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = company_fixture(self, [DEVELOPER])

    def test_server_key_is_not_rendered(self):
        with patch.dict(os.environ, {'GROQ_API_KEY': 'server-test-key'}):
            app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key='groq_key').value, '')
        self.assertFalse(app.chat_input[0].disabled)

    def test_initial_modes_without_credentials(self):
        app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(app.chat_input[0].disabled)
        app.sidebar.radio[0].set_value('Document Chat').run()
        self.assertFalse(app.exception)
        self.assertEqual(app.button(key='index_documents').label, 'Index documents')
        self.assertEqual(app.text_input(key='groq_key').label, 'Groq API key')
        self.assertEqual(app.text_input(key='model_name').value, 'llama-3.3-70b-versatile')

    def test_document_index_without_credentials(self):
        app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        app.sidebar.radio[0].set_value('Document Chat').run()
        app.button(key='index_documents').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertTrue(app.success)
        self.assertGreater(len(app.session_state['document_index']['vectors']), 0)
        self.assertTrue(app.chat_input[0].disabled)

    def test_sql_chat_rendering_and_clear(self):
        app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        app.text_input(key='groq_key').set_value('test-key').run()
        with patch('querypilot.answer_sql', return_value={
                'sql': 'SELECT 1', 'columns': ['value'], 'rows': [(1,)], 'truncated': False}):
            app.chat_input[0].set_value('Test question').run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.chat_message), 2)
        self.assertEqual(len(app.dataframe), 2)  # Membership table and SQL results.
        app.button(key='clear_conversation').click().run()
        self.assertEqual(len(app.chat_message), 0)

    def test_document_index_and_answer(self):
        app = AppTest.from_file('streamlit_app.py').run(timeout=30)
        app.sidebar.radio[0].set_value('Document Chat').run()
        app.text_input(key='groq_key').set_value('test-key').run()
        index = {'chunks': [{'source': 'SOLID', 'text': 'One responsibility.'}], 'vectors': [[1]]}
        with patch('querypilot.build_document_index', return_value=index):
            app.button(key='index_documents').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.chat_input[0].disabled)
        with patch('querypilot.answer_documents', return_value={
                'answer': 'One responsibility [1].', 'sources': index['chunks']}):
            app.chat_input[0].set_value('Explain SOLID').run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.chat_message), 2)


if __name__ == '__main__':
    unittest.main()
