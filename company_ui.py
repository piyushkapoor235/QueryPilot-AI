"""Company selection and membership UI; authorization lives in CompanyStore."""

import os

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from tenancy import AccessDenied, CompanyStore, clear_company_state, database_url


@st.cache_resource
def get_store(url, initialize):
    # Source-metadata checkpoint: initialize includes company_data_sources in development.
    return CompanyStore(url, initialize=initialize)


def require_company(principal):
    try:
        store = get_store(database_url(), os.getenv('APP_ENV') == 'development')
        store.register(principal)
        available = store.list_companies(principal)
    except (ValueError, SQLAlchemyError):
        clear_company_state(st.session_state, None, None)
        st.error('Company storage is unavailable. Configure the application database and initialize its schema.')
        st.stop()

    ids = [company['id'] for company in available]
    if st.session_state.get('company_selector') not in ids:
        st.session_state.pop('company_selector', None)
    selected = None
    role = None
    if available:
        labels = {company['id']: f"{company['name']} ({company['id'][:8]})" for company in available}
        selected = st.sidebar.selectbox('Company', ids, format_func=lambda value: labels[value],
                                       key='company_selector')
        try:
            role = store.require(principal, selected)
        except AccessDenied:
            clear_company_state(st.session_state, None, None)
            st.error('Company access has changed. Reload to continue.')
            st.stop()
    clear_company_state(st.session_state, selected, role)

    with st.sidebar.expander('Create or join a company', expanded=not available):
        with st.form('create_company'):
            name = st.text_input('Company name', max_chars=100)
            create = st.form_submit_button('Create company')
        if create:
            try:
                store.create_company(principal, name)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        with st.form('join_company'):
            token = st.text_input('Invitation code', type='password')
            join = st.form_submit_button('Join company')
        if join:
            try:
                store.accept_invitation(principal, token)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if selected is None:
        st.info('Create a company or accept an invitation to open your workspace.')
        st.stop()
    st.sidebar.caption(f'Company role: {role.title()}')
    return store, selected, role


def render_company_admin(store, principal, company_id):
    with st.expander('Company administration'):
        members = store.list_members(principal, company_id)
        st.dataframe([{'Email': member['email'], 'Role': member['role']} for member in members],
                     hide_index=True)
        with st.form('invite_member'):
            email = st.text_input('Invite email', max_chars=320)
            invited_role = st.selectbox('Invitation role', ['user', 'admin'])
            invite = st.form_submit_button('Create invitation')
        if invite:
            try:
                token = store.invite(principal, company_id, email, invited_role)
                st.info('Share this single-use code privately with the invited person. It expires in 24 hours. No email has been sent.')
                st.code(token, language=None)
            except ValueError as exc:
                st.error(str(exc))
        member_labels = {member['id']: member['email'] for member in members}
        with st.form('change_member_role'):
            target = st.selectbox('Member', list(member_labels),
                                  format_func=lambda value: member_labels[value])
            new_role = st.selectbox('New role', ['user', 'admin'])
            change = st.form_submit_button('Update role')
        if change:
            try:
                store.set_role(principal, company_id, target, new_role)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
