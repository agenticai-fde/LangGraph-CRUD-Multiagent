"""Supervisor routes each request to a read, create or update agent, or to a
delete flow that pauses for human confirmation (LangGraph interrupt).

supervisor -> read | create | update -> END
           -> delete (find customer) -> delete_confirm (interrupt) -> END
"""
import re
from typing import Literal, Optional, TypedDict

from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from config import get_llm
from tools import add_customer, delete_customer_by_id, find_customer, get_customers, update_customer

ROUTES = ("read", "create", "update", "delete")
llm = get_llm()

read_agent = create_agent(llm, [get_customers], system_prompt=(
    "You look up customers. Always call get_customers, then summarise the rows. Never invent data."))
create_customer_agent = create_agent(llm, [add_customer], system_prompt=(
    "You add customers. Call add_customer with the name, city and email from the request, "
    "then confirm what was added."))
update_agent = create_agent(llm, [update_customer], system_prompt=(
    "You update customers by numeric id. Call update_customer with only the fields the user "
    "wants changed, then report the result."))


class State(TypedDict, total=False):
    request: str
    route: str
    result: str
    pending_delete: Optional[dict]


class Route(BaseModel):
    agent: Literal["read", "create", "update", "delete"]


class DeleteTarget(BaseModel):
    customer_id: Optional[int] = Field(None, description="Numeric id of the customer to delete")


def supervisor(state: State) -> dict:
    prompt = ("Choose exactly one agent for this customer-database request: read (look up / list), "
              "create (add new), update (change existing), delete (remove).\n"
              f"Request: {state['request']}")
    try:
        route = llm.with_structured_output(Route).invoke(prompt).agent
    except Exception:
        text = llm.invoke(prompt + "\nReturn only the agent name.").content.lower()
        route = next((r for r in ROUTES if r in text), "read")
    return {"route": route}


def run_agent(agent):
    def node(state: State) -> dict:
        response = agent.invoke({"messages": [("user", state["request"])]})
        return {"result": response["messages"][-1].content}
    return node


def extract_customer_id(request: str) -> Optional[int]:
    numbers = re.findall(r"\b\d+\b", request)  # deterministic first
    if len(numbers) == 1:
        return int(numbers[0])
    try:
        return llm.with_structured_output(DeleteTarget).invoke(
            f"Extract the customer id to delete from: {request}").customer_id
    except Exception:
        return None


def delete_prepare(state: State) -> dict:
    customer_id = extract_customer_id(state["request"])
    if customer_id is None:
        return {"pending_delete": None, "result": "Please give the numeric id of the customer to delete."}
    customer = find_customer(customer_id)
    if not customer:
        return {"pending_delete": None, "result": f"No customer with id {customer_id}."}
    return {"pending_delete": customer}


def delete_confirm(state: State) -> dict:
    customer = state["pending_delete"]
    approved = interrupt({
        "action": "delete",
        "customer": customer,
        "question": f"Delete customer {customer['id']} ({customer['name']}, {customer['city']})? "
                    "Send confirm=true to proceed.",
    })
    if approved is True:
        try:
            delete_customer_by_id(customer["id"])
        except APIError:
            return {"pending_delete": None,
                    "result": f"Cannot delete customer {customer['id']}: they still have orders, "
                              "tickets or accounts linked to them."}
        return {"pending_delete": None, "result": f"Deleted customer {customer['id']} ({customer['name']})."}
    return {"pending_delete": None, "result": "Delete cancelled."}


builder = StateGraph(State)
builder.add_node("supervisor", supervisor)
builder.add_node("read", run_agent(read_agent))
builder.add_node("create", run_agent(create_customer_agent))
builder.add_node("update", run_agent(update_agent))
builder.add_node("delete", delete_prepare)
builder.add_node("delete_confirm", delete_confirm)

builder.add_edge(START, "supervisor")
builder.add_conditional_edges("supervisor", lambda s: s["route"], {r: r for r in ROUTES})
builder.add_conditional_edges("delete", lambda s: "delete_confirm" if s.get("pending_delete") else END,
                              ["delete_confirm", END])
for node in ("read", "create", "update", "delete_confirm"):
    builder.add_edge(node, END)

# InMemorySaver keeps pending confirmations only while the server runs.
graph = builder.compile(checkpointer=InMemorySaver())
