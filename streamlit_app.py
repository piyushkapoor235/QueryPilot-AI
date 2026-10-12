"""QueryPilot's Python portfolio interface."""

import hashlib
import io
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pypdf import PdfReader

from querypilot import answer_documents, answer_sql, build_document_index, database_schema

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="QueryPilot", page_icon="💬", layout="wide")
st.title("QueryPilot")
st.caption("Ask your database. Explore your documents.")


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
    api_key = st.text_input("OpenAI API key", type="password", value=secret("OPENAI_API_KEY"))
    model_name = st.text_input("Model", value=secret("OPENAI_MODEL", "gpt-4.1-mini"))
    st.caption("Questions and retrieved data are sent to OpenAI. Use sample or non-sensitive data.")
    uploads = []
    if mode == "Document Chat":
        uploads = st.file_uploader("Upload PDF, TXT, or Markdown", type=["pdf", "txt", "md"],
                                   accept_multiple_files=True)
        st.caption("Without uploads, the bundled SOLID principles document is used.")
    if st.button("Clear conversation", width="stretch"):
        st.session_state[f"messages_{mode}"] = []
        st.rerun()

history_key = f"messages_{mode}"
st.session_state.setdefault(history_key, [])
messages = st.session_state[history_key]

if mode == "SQL Chat":
    st.info("Query the bundled Northwind database. Queries run in read-only mode.")
    with st.expander("Database schema"):
        st.code(database_schema(), language="sql")
    st.caption('Try: "Which five products have the highest unit price?"')
else:
    identity = hashlib.sha256()
    for upload in uploads:
        identity.update(upload.name.encode())
        identity.update(upload.getvalue())
    document_id = identity.hexdigest() if uploads else "bundled-solid"
    if st.session_state.get("document_id") != document_id:
        st.session_state["document_id"] = document_id
        st.session_state.pop("document_index", None)
        messages.clear()
    st.caption('Try: "Explain the single responsibility principle."')
    if st.button("Index documents", type="primary", key="index_documents"):
        if not api_key:
            st.warning("Enter your API key first.")
        else:
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
                        sources = [("SOLID principles", (ROOT / "VECTORSTORE-AGENT" /
                                    "Solid_Principles.txt").read_text(encoding="utf-8"))]
                    embedding_model = OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key)
                    new_index = build_document_index(sources, embedding_model)
                    st.session_state["document_index"] = new_index
                    messages.clear()
                st.success(f"Indexed {len(new_index['chunks'])} text chunks.")
            except Exception as exc:
                st.error(f"Indexing failed ({type(exc).__name__}). Check your key and document format.")
    if "document_index" in st.session_state:
        st.caption("Documents are indexed for this browser session.")


def render_message(message):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if "sql" in message:
            st.code(message["sql"], language="sql")
            import pandas as pd
            st.dataframe(pd.DataFrame(message["rows"], columns=message["columns"]),
                         width="stretch")
            if message["truncated"]:
                st.caption("Showing the first 200 rows.")
        if "sources" in message:
            with st.expander("Retrieved sources"):
                for i, source in enumerate(message["sources"], 1):
                    st.markdown(f"**[{i}] {source['source']}**")
                    st.text(source["text"])


for message in messages:
    render_message(message)

disabled = not api_key or not model_name or (mode == "Document Chat" and
                                            "document_index" not in st.session_state)
if not api_key:
    st.caption("Enter an OpenAI API key in the sidebar to start chatting.")
if question := st.chat_input("Ask a question…", disabled=disabled):
    recent = [{"role": m["role"], "content": m["content"]} for m in messages[-6:]]
    user_message = {"role": "user", "content": question}
    messages.append(user_message)
    render_message(user_message)
    try:
        with st.spinner("Finding an answer…"):
            model = ChatOpenAI(model=model_name, api_key=api_key, temperature=0,
                               timeout=45, max_retries=1)
            if mode == "SQL Chat":
                result = answer_sql(model, question, recent)
                response = {"role": "assistant", "content": f"Returned {len(result['rows'])} rows.",
                            **result}
            else:
                embeddings = OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key)
                result = answer_documents(model, embeddings, question, recent,
                                          st.session_state["document_index"])
                response = {"role": "assistant", "content": result["answer"],
                            "sources": result["sources"]}
        messages.append(response)
        render_message(response)
    except Exception as exc:
        st.error(f"Request failed ({type(exc).__name__}). Check your API key, model access, "
                 "and quota, or try rephrasing your question.")
