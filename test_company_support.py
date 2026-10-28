"""Isolated application database fixture for Streamlit tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from authentication import Principal
from tenancy import CompanyStore


def company_fixture(test, principals):
    folder = TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    store = CompanyStore('sqlite:///' + (Path(folder.name) / 'app.db').as_posix(), initialize=True)
    test.addCleanup(store.engine.dispose)
    for principal in principals:
        store.register(principal)
        store.create_company(principal, 'Test company')
    factory = patch('company_ui.get_store', return_value=store)
    factory.start()
    test.addCleanup(factory.stop)
    # URL validation remains active; factory redirects only actual storage.
    url = patch('company_ui.database_url', return_value='sqlite:///test-fixture.db')
    url.start()
    test.addCleanup(url.stop)
    return store


DEVELOPER = Principal('local-development', 'developer', 'developer@localhost')
