"""Admin publication and member selection of company-scoped SQL sources."""

import json

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from data_connections import (CompanySQL, configured_references, discover_schema,
                              publish_source)


def render_data_source_admin(store, principal, company_id):
    with st.expander('Company SQL data sources'):
        st.caption(f'Company ID for connection provisioning: {company_id}')
        try:
            references = configured_references(company_id)
            sources = store.list_data_sources(principal, company_id)
        except (ValueError, SQLAlchemyError):
            st.error('Data source configuration is unavailable.')
            return
        if not references:
            st.info('No PostgreSQL connection has been provisioned for this company. Ask the platform operator to assign a read-only connection.')
            return
        source_labels = {source['id']: source['name'] for source in sources}
        selection = st.selectbox('Configure source', ['new'] + list(source_labels),
            format_func=lambda value: 'New source' if value == 'new' else source_labels[value],
            key='admin_source_selection')
        current = (store.get_data_source(principal, company_id, selection, admin=True)
                   if selection != 'new' else None)
        # A fresh form key prevents old settings from being submitted for a different source.
        context = selection + ':' + str(current['revision'] if current else 0)
        with st.form('data_source_form:' + context):
            name = st.text_input('Data source name', value=current['name'] if current else '', max_chars=100)
            options = list(references)
            reference = st.selectbox('Provisioned connection', options,
                index=options.index(current['secret_ref']) if current and current['secret_ref'] in options else 0)
            schema = st.text_input('PostgreSQL schema', value=current['schema_name'] if current else 'public')
            permissions = st.text_area('Approved tables and columns (JSON)',
                value=json.dumps(current['allowed_columns'], indent=2) if current else '{}',
                help='Example: {"products": ["id", "name", "price"]}. Only these columns are exposed to chat.')
            inspect = st.form_submit_button('Inspect readable schema')
            save = st.form_submit_button('Test and publish permissions')
        if inspect or save:
            try:
                if inspect:
                    readable = discover_schema(store, principal, company_id, reference, schema)
                    st.json(readable)
                else:
                    publish_source(store, principal, company_id, name, reference, schema,
                        json.loads(permissions), source_id=current['id'] if current else None,
                        expected_revision=current['revision'] if current else None)
                    st.rerun()
            except ValueError as exc:
                # JSON parser messages contain only the user's own permission input.
                st.error(str(exc))
            except SQLAlchemyError:
                st.error('Unable to save data source. Use a unique name and check application storage.')
        if current:
            with st.form('delete_data_source:' + context):
                confirmed = st.checkbox('Confirm removal of this SQL data source')
                remove = st.form_submit_button('Remove data source')
            if remove:
                if not confirmed:
                    st.error('Confirm removal before continuing.')
                else:
                    try:
                        store.delete_data_source(principal, company_id, current['id'])
                        st.rerun()
                    except (ValueError, SQLAlchemyError):
                        st.error('Unable to remove data source. Reload and check your access.')


def select_sql_source(store, principal, company_id):
    """Return backend/fingerprint/schema; a configured-source failure never falls back to sample."""
    sources = store.list_data_sources(principal, company_id)
    labels = {source['id']: source['name'] for source in sources}
    options = list(labels) + ['sample']
    if st.session_state.get('sql_source_selector') not in options:
        st.session_state.pop('sql_source_selector', None)
    chosen = st.selectbox('SQL data source', options,
        format_func=lambda value: 'Public Northwind sample' if value == 'sample' else labels[value],
        key='sql_source_selector')
    backend, fingerprint, schema = None, 'sample', None
    try:
        if chosen != 'sample':
            backend = CompanySQL(store, principal, company_id, chosen)
            fingerprint = backend.fingerprint()
            schema = backend.schema()
        boundary = (company_id, chosen, fingerprint)
    except ValueError:
        boundary = (company_id, chosen, 'unavailable')
        if st.session_state.get('_sql_boundary') != boundary:
            st.session_state['messages_SQL Chat'] = []
        st.session_state['_sql_boundary'] = boundary
        st.error('This company data source is unavailable or access changed. Ask an Admin to review its connection and permissions.')
        return None, None, None
    if st.session_state.get('_sql_boundary') != boundary:
        st.session_state['messages_SQL Chat'] = []
    st.session_state['_sql_boundary'] = boundary
    return backend, fingerprint, schema
