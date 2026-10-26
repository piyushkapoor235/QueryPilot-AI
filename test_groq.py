"""Exercise Groq serialization and response parsing without paid or live requests."""

import json
import unittest

import httpx
from langchain_groq import ChatGroq

from local_embeddings import LocalEmbeddings
from querypilot import answer_documents, answer_sql, build_document_index


class GroqTests(unittest.TestCase):
    def test_sql_tool_call_and_document_answer(self):
        requests = []

        def respond(request):
            self.assertEqual(request.url.host, 'api.groq.com')
            self.assertEqual(request.url.path, '/openai/v1/chat/completions')
            body = json.loads(request.content)
            requests.append(body)
            if 'tools' in body:
                name = body['tools'][0]['function']['name']
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'query_1', 'type': 'function', 'function': {
                        'name': name,
                        'arguments': json.dumps({'sql': 'SELECT COUNT(*) AS count FROM Employees'}),
                    },
                }]}
                reason = 'tool_calls'
            else:
                message = {'role': 'assistant', 'content': 'One responsibility [1].'}
                reason = 'stop'
            return httpx.Response(200, json={
                'id': 'chat-test', 'object': 'chat.completion', 'created': 1,
                'model': body['model'],
                'choices': [{'index': 0, 'message': message, 'finish_reason': reason}],
            })

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            model = ChatGroq(model='llama-3.3-70b-versatile', api_key='test-key',
                             http_client=client, max_retries=0, temperature=0)
            sql = answer_sql(model, 'How many employees?', [])
            self.assertGreater(sql['rows'][0][0], 0)
            embeddings = LocalEmbeddings()
            index = build_document_index([
                ('SOLID', 'The single responsibility principle means one responsibility.')
            ], embeddings)
            answer = answer_documents(model, embeddings, 'Explain single responsibility', [], index)
        self.assertEqual(answer['answer'], 'One responsibility [1].')
        self.assertEqual(answer['sources'][0]['source'], 'SOLID')
        self.assertEqual(len(requests), 2)


if __name__ == '__main__':
    unittest.main()
