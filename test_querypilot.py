import sqlite3
import unittest
from unittest.mock import patch

from querypilot import (answer_documents, answer_sql, build_document_index, chunk_text,
                        cosine_similarity, execute_query)


class QueryPilotTests(unittest.TestCase):
    def test_northwind_query(self):
        result = execute_query('SELECT COUNT(*) AS count FROM Employees')
        self.assertGreater(result['rows'][0][0], 0)
        self.assertEqual(result['columns'], ['count'])

    def test_mutations_and_attachment_blocked(self):
        for sql in ['DELETE FROM Employees', 'DROP TABLE Employees',
                    "ATTACH DATABASE ':memory:' AS other", 'PRAGMA user_version = 5']:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.Error):
                execute_query(sql)
        self.assertGreater(execute_query('SELECT COUNT(*) FROM Employees')['rows'][0][0], 0)

    def test_limit_and_cte(self):
        result = execute_query('WITH sample AS (SELECT * FROM Products) SELECT * FROM sample', limit=2)
        self.assertEqual(len(result['rows']), 2)
        self.assertTrue(result['truncated'])

    def test_multiple_statements_and_expensive_query(self):
        with self.assertRaises(sqlite3.Error):
            execute_query('SELECT 1; SELECT 2')
        with self.assertRaises(sqlite3.Error):
            execute_query('SELECT COUNT(*) FROM Products a, Products b, Products c, Products d',
                          timeout=0)

    def test_retry(self):
        with patch('querypilot.generate_sql', side_effect=['SELECT missing FROM Employees',
                                                         'SELECT COUNT(*) FROM Employees']) as generate:
            result = answer_sql(None, 'How many employees?', [])
        self.assertEqual(generate.call_count, 2)
        self.assertGreater(result['rows'][0][0], 0)

    def test_document_chunks_and_embeddings(self):
        class Embeddings:
            def embed_documents(self, texts):
                return [[1, 0] for _ in texts]
        index = build_document_index([('example.txt', 'x' * 2500)], Embeddings())
        self.assertEqual(len(index['chunks']), len(index['vectors']))
        self.assertEqual(index['chunks'][0]['source'], 'example.txt')
        self.assertEqual(cosine_similarity([1, 0], [1, 0]), 1)
        self.assertEqual(cosine_similarity([0, 0], [1, 0]), 0)
        with self.assertRaises(ValueError):
            build_document_index([('blank.txt', ' ')], Embeddings())
        with self.assertRaises(ValueError):
            chunk_text('text', size=100, overlap=100)

    def test_retrieval_ranking_and_citation_context(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        model = Mock()
        model.invoke.return_value = SimpleNamespace(content='Relevant answer [1].')
        embeddings = Mock()
        embeddings.embed_query.return_value = [1, 0]
        result = answer_documents(model, embeddings, 'Question', [], {
            'chunks': [{'source': 'irrelevant', 'text': 'Other'},
                       {'source': 'relevant', 'text': 'Answer'}],
            'vectors': [[0, 1], [1, 0]],
        })
        self.assertEqual(result['sources'][0]['source'], 'relevant')
        self.assertIn('[1] relevant', model.invoke.call_args.args[0][1][1])


if __name__ == '__main__':
    unittest.main()
