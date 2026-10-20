"""Terminal client for the running API.

    python client.py "show all customers"
    python client.py                        interactive; blank line to quit
"""
import os
import sys

import httpx

import config  # noqa: F401  (loads the repo-root .env)

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8006")
HEADERS = {"X-API-Key": os.getenv("API_KEY", "")}
TIMEOUT = 600  # a local LLM on CPU can take minutes


def post(path: str, body: dict) -> dict:
    try:
        r = httpx.post(f"{BASE_URL}{path}", json=body, headers=HEADERS, timeout=TIMEOUT)
    except httpx.ConnectError:
        return {"status": "error", "result": f"API not running at {BASE_URL}. "
                "Start it with: uvicorn main:app --port 8006"}
    if r.status_code != 200:
        return {"status": "error", "result": f"HTTP {r.status_code}: {r.json().get('detail')}"}
    return r.json()


def ask(message: str) -> None:
    print("... thinking")
    data = post("/ask", {"message": message})
    if data["status"] == "needs_confirmation":
        answer = input(data["confirmation"]["question"].split("?")[0] + "? [y/N] ")
        data = post("/confirm", {"thread_id": data["thread_id"],
                                 "confirm": answer.strip().lower() in ("y", "yes")})
    print(f"[{data.get('route', '-')}] {data['result']}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ask(" ".join(sys.argv[1:]))
    else:
        while msg := input("You: ").strip():
            ask(msg)
