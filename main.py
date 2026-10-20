"""FastAPI service exposing the multi-agent graph.

    uvicorn main:app --port 8006      then open http://127.0.0.1:8006/docs
"""
import os
import uuid
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.security import APIKeyHeader
from langgraph.types import Command
from ollama import ResponseError
from pydantic import BaseModel, Field

import config  # noqa: F401  (loads the repo-root .env)
from graph import graph

PUBLIC_URL = os.getenv("PUBLIC_URL")  # e.g. an ngrok URL, for tools like Microsoft Foundry
API_KEY = os.getenv("API_KEY")        # when set, every request needs header X-API-Key

app = FastAPI(
    title="Supabase Multi-Agent",
    description="Natural-language CRUD over the customers table (id, name, city, email, plan).",
    servers=[{"url": PUBLIC_URL, "description": "Public"},
             {"url": "/", "description": "Same host as /docs"}] if PUBLIC_URL else None,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type", "X-API-Key", "ngrok-skip-browser-warning"])

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: Optional[str] = Security(api_key_header)):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")


def custom_openapi():
    # Declare the API key globally too: Microsoft Foundry's OpenAPI tool reads it.
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description,
                         routes=app.routes, servers=app.servers)
    if API_KEY:
        schema["security"] = [{"APIKeyHeader": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


class AskRequest(BaseModel):
    message: str = Field(description="Plain-English request, e.g. 'Show customers from Delhi'.")


class ConfirmRequest(BaseModel):
    thread_id: str = Field(description="thread_id returned by askCustomerDb with needs_confirmation.")
    confirm: bool = Field(description="true to delete, false to cancel. Only true if the user agreed.")


class AgentResponse(BaseModel):
    thread_id: str
    status: Literal["done", "needs_confirmation"]
    route: Optional[str] = Field(None, description="read, create, update or delete")
    result: Optional[str] = Field(None, description="Answer when status is done")
    confirmation: Optional[dict] = Field(None, description="Customer to delete and the question to show")


def to_response(thread_id: str, state: dict) -> AgentResponse:
    if state.get("__interrupt__"):
        return AgentResponse(thread_id=thread_id, status="needs_confirmation", route=state.get("route"),
                             confirmation=state["__interrupt__"][0].value)
    return AgentResponse(thread_id=thread_id, status="done", route=state.get("route"),
                         result=state.get("result"))


@app.post("/ask", operation_id="askCustomerDb", response_model=AgentResponse,
          dependencies=[Depends(require_api_key)],
          summary="Read, add, update or delete customers using a plain-English request",
          description="Deletes are never executed directly: they return needs_confirmation with a "
                      "thread_id. Show the question to the user and call confirmDelete with their answer.")
def ask(data: AskRequest):
    thread_id = str(uuid.uuid4())
    config_ = {"configurable": {"thread_id": thread_id}}
    try:
        state = graph.invoke({"request": data.message}, config_)
    except ResponseError as e:
        raise HTTPException(status_code=503, detail=f"LLM unavailable: {e.error}")
    return to_response(thread_id, state)


@app.post("/confirm", operation_id="confirmDelete", response_model=AgentResponse,
          dependencies=[Depends(require_api_key)],
          summary="Confirm or cancel a pending customer delete",
          description="Call only after askCustomerDb returned needs_confirmation and the user answered.")
def confirm(data: ConfirmRequest):
    config_ = {"configurable": {"thread_id": data.thread_id}}
    if not graph.get_state(config_).interrupts:
        raise HTTPException(status_code=404, detail="No pending confirmation for this thread_id.")
    state = graph.invoke(Command(resume=data.confirm), config_)
    return to_response(data.thread_id, state)


@app.get("/health")
def health():
    return {"status": "ok"}
