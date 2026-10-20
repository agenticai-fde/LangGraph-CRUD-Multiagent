"""Customer CRUD tools. Deletion is NOT an LLM tool: the graph asks the user to
confirm first and then calls delete_customer_by_id itself."""
from typing import Optional

from langchain_core.tools import tool

from config import get_db

COLUMNS = "id,name,city,email,plan"


@tool
def get_customers(city: str = "") -> str:
    """Find customers in a city. Use an empty string for all cities."""
    query = get_db().table("customers").select(COLUMNS).order("id")
    if city:
        query = query.ilike("city", city.strip())
    return str(query.execute().data)


@tool
def add_customer(name: str, city: str, email: str) -> str:
    """Insert a new customer."""
    result = get_db().table("customers").insert({
        "name": name.strip(), "city": city.strip(), "email": email.strip().lower(),
    }).execute()
    return str(result.data)


@tool
def update_customer(customer_id: int, name: Optional[str] = None, city: Optional[str] = None,
                    email: Optional[str] = None, plan: Optional[str] = None) -> str:
    """Update a customer's name, city, email and/or plan (basic, premium, enterprise).
    Pass only the fields to change."""
    changes = {k: v.strip() for k, v in
               {"name": name, "city": city, "email": email, "plan": plan}.items() if v}
    if not changes:
        return "Nothing to update: provide name, city, email or plan."
    result = get_db().table("customers").update(changes).eq("id", customer_id).execute()
    return str(result.data) if result.data else f"No customer with id {customer_id}."


def find_customer(customer_id: int) -> Optional[dict]:
    rows = get_db().table("customers").select(COLUMNS).eq("id", customer_id).execute().data
    return rows[0] if rows else None


def delete_customer_by_id(customer_id: int) -> list:
    return get_db().table("customers").delete().eq("id", customer_id).execute().data
