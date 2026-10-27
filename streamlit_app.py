"""QueryPilot's Python portfolio interface."""

import hashlib
import io
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from langchain_groq import ChatGroq

from authentication import require_authentication
from company_ui import require_company, render_company_admin
from data_source_ui import render_data_source_admin, select_sql_source
from local_embeddings import LocalEmbeddings
from pypdf import PdfReader

from querypilot import answer_documents, answer_sql, build_document_index, database_schema

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="QueryPilot", page_icon="💬", layout="wide")
st.title("QueryPilot")
st.caption("Ask your database. Explore your documents.")

principal = require_authentication()
store, company_id, company_role = require_company(principal)


def secret(name, default=""):
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name, default)
    except FileNotFoundError:
        return default


with st.sidebar:
    st.header("Workspace")
    mode = st.radio("Choose a mode", ["SQL Chat", "Document Chat"])
    supplied_key = st.text_input("Groq API key", type="password", key='groq_key')
    api_key = supplied_key or secret("GROQ_API_KEY")
    model_name = st.text_input("Model", value=secret("GROQ_MODEL", "llama-3.3-70b-versatile"), key='model_name')
    st.caption("Questions and retrieved data are sent to Groq. Use sample or non-sensitive data.")
    uploads = []
    if mode == "Document Chat" and company_role == 'admin':
        uploads = st.file_uploader("Upload PDF, TXT, or Markdown", type=["pdf", "txt", "md"],
                                   accept_multiple_files=True)
        st.caption("Without uploads, existing company documents are used; an empty company uses the public SOLID sample.")
    if st.button("Clear conversation", width="stretch", key='clear_conversation'):
        st.session_state[f"messages_{mode}"] = []
        st.rerun()

if company_role == 'admin':
    render_company_admin(store, principal, company_id)
    render_data_source_admin(store, principal, company_id)

sql_backend, sql_fingerprint, approved_schema = None, 'sample', None
if mode == 'SQL Chat':
    sql_backend, sql_fingerprint, approved_schema = select_sql_source(store, principal, company_id)

history_key = f"messages_{mode}"
st.session_state.setdefault(history_key, [])
messages = st.session_state[history_key]

if mode == "SQL Chat":
    store.require(principal, company_id)
    if sql_fingerprint == 'sample':
        st.info("Public sample data: Northwind. This is not your company's database. Queries run in read-only mode.")
    elif sql_fingerprint:
        st.info('Company PostgreSQL data. Only Admin-approved tables and columns are available; queries are read-only.')
    with st.expander("Database schema"):
        if sql_fingerprint:
            st.code(approved_schema if sql_backend else database_schema(), language="sql")
    if sql_fingerprint == 'sample':
        st.caption('Try: "Which five products have the highest unit price?"')
else:
    published_sources = store.read_documents(principal, company_id)
    published_identity = hashlib.sha256(repr(published_sources).encode()).hexdigest()
    identity = hashlib.sha256()
    for upload in uploads:
        identity.update(upload.name.encode())
        identity.update(upload.getvalue())
    document_id = identity.hexdigest() if uploads else published_identity
    if st.session_state.get("document_id") != document_id:
        st.session_state["document_id"] = document_id
        st.session_state.pop("document_index", None)
        st.session_state.pop('published_identity', None)
        messages.clear()
    st.caption('Try: "Explain the single responsibility principle."')
    st.caption("Indexing runs locally using word matching; no API key or model download is needed.")
    if company_role != 'admin':
        st.caption('Shared documents are managed by your company Admin.')
    if company_role == 'admin' and st.button("Index documents", type="primary", key="index_documents"):
        try:
            with st.spinner("Reading and indexing documents…"):
                if sum(upload.size for upload in uploads) > 10 * 1024 * 1024:
                    raise ValueError("Please upload at most 10 MB in total.")
                sources = []
                for upload in uploads:
                    if upload.name.lower().endswith(".pdf"):
                        reader = PdfReader(io.BytesIO(upload.getvalue()))
                        sources.extend((f"{upload.name}, page {i}", page.extract_text() or "")
                                       for i, page in enumerate(reader.pages, 1))
                    else:
                        sources.append((upload.name, upload.getvalue().decode("utf-8-sig")))
                if not uploads:
                    sources = published_sources or [("SOLID principles (public sample)",
                        (ROOT / "VECTORSTORE-AGENT" / "Solid_Principles.txt").read_text(encoding="utf-8"))]
                embedding_model = LocalEmbeddings()
                new_index = build_document_index(sources, embedding_model)
                store.replace_documents(principal, company_id, sources)
                st.session_state['published_identity'] = hashlib.sha256(repr(sources).encode()).hexdigest()
                if not uploads:
                    st.session_state['document_id'] = st.session_state['published_identity']
                st.session_state["document_index"] = new_index
                messages.clear()
            st.success(f"Indexed {len(new_index['chunks'])} text chunks.")
        except Exception as exc:
            st.error(f"Indexing failed ({type(exc).__name__}). Check your document format.")
    if not uploads and published_sources and st.session_state.get('published_identity') != published_identity:
        st.session_state['document_index'] = build_document_index(published_sources, LocalEmbeddings())
        st.session_state['published_identity'] = published_identity
    if "document_index" in st.session_state:
        st.caption("Documents are shared within this company. The retrieval index is held in this session.")


def render_message(message):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if "sql" in message:
            st.code(message["sql"], language="sql")
            import pandas as pd
            st.dataframe(pd.DataFrame(message["rows"], columns=message["columns"]),
                         width="stretch")
            if message["truncated"]:
                st.caption("Results truncated to the configured row or response-size limit.")
        if "sources" in message:
            with st.expander("Retrieved sources"):
                for i, source in enumerate(message["sources"], 1):
                    st.markdown(f"**[{i}] {source['source']}**")
                    st.text(source["text"])


for message in messages:
    render_message(message)

disabled = not api_key or not model_name or (mode == "Document Chat" and
                                            "document_index" not in st.session_state) or (mode == 'SQL Chat' and not sql_fingerprint)
if not api_key:
    st.caption("Enter a Groq API key in the sidebar to start chatting.")
if question := st.chat_input("Ask a question…", disabled=disabled):
    recent = [{"role": m["role"], "content": m["content"]} for m in messages[-6:]]
    user_message = {"role": "user", "content": question}
    messages.append(user_message)
    render_message(user_message)
    try:
        store.require(principal, company_id)
        with st.spinner("Finding an answer…"):
            model = ChatGroq(model=model_name, api_key=api_key, temperature=0,
                               timeout=45, max_retries=1)
            if mode == "SQL Chat":
                result = (sql_backend.answer(model, question, recent, expected_fingerprint=sql_fingerprint)
                          if sql_backend else answer_sql(model, question, recent))
                response = {"role": "assistant", "content": f"Returned {len(result['rows'])} rows.",
                            **result}
            else:
                embeddings = LocalEmbeddings()
                result = answer_documents(model, embeddings, question, recent,
                                          st.session_state["document_index"])
                response = {"role": "assistant", "content": result["answer"],
                            "sources": result["sources"]}
        store.require(principal, company_id)
        messages.append(response)
        render_message(response)
    except Exception as exc:
        st.error(f"Request failed ({type(exc).__name__}). Check your API key, model access, "
                 "and quota, or try rephrasing your question.")
