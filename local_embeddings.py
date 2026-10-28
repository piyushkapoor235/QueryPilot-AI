"""Offline word-based vectors for document retrieval; no external model or API."""

from sklearn.feature_extraction.text import HashingVectorizer


class LocalEmbeddings:
    """Stable vectors shared by document chunks and questions across app reruns."""

    def __init__(self):
        self.vectorizer = HashingVectorizer(
            n_features=4096, alternate_sign=False, ngram_range=(1, 2),
            stop_words="english", norm="l2",
        )

    def embed_documents(self, texts):
        return self.vectorizer.transform(texts).toarray().tolist()

    def embed_query(self, text):
        return self.embed_documents([text])[0]
