import os
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

DEVIN_API_BASE = "https://api.devin.ai/v1"

mcp = FastMCP("Devin Session Server")


def get_api_key() -> str:
    api_key = os.environ.get("DEVIN_API_KEY")
    if not api_key:
        raise ToolError(
            "DEVIN_API_KEY environment variable is not set. "
            "Please set it to your Devin API key (starts with 'apk_')."
        )
    return api_key


@mcp.tool
async def get_session(session_id: str) -> dict[str, Any]:
    """Retrieve details about an existing Devin session.

    Returns session information including status, messages, timestamps, and metadata.

    Args:
        session_id: The identifier for the Devin session to retrieve.
    """
    api_key = get_api_key()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DEVIN_API_BASE}/sessions/{session_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )

        if response.status_code == 404:
            raise ToolError(f"Session '{session_id}' not found.")
        elif response.status_code == 401:
            raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")
        elif response.status_code == 422:
            raise ToolError(f"Invalid session ID format: {session_id}")
        elif response.status_code != 200:
            raise ToolError(
                f"Devin API error (status {response.status_code}): {response.text}"
            )

        return response.json()


@mcp.tool
async def create_session(
    prompt: str,
    title: str | None = None,
    snapshot_id: str | None = None,
    playbook_id: str | None = None,
    tags: list[str] | None = None,
    max_acu_limit: int | None = None,
    idempotent: bool = False,
    unlisted: bool = False,
) -> dict[str, Any]:
    """Create a new Devin session.

    Returns the session ID and URL for the newly created session.

    Args:
        prompt: The instruction for Devin to execute.
        title: Custom session name. Auto-generated if not provided.
        snapshot_id: Restore from a previous snapshot.
        playbook_id: Associated playbook identifier.
        tags: Session categorization labels.
        max_acu_limit: Resource consumption ceiling (positive integer).
        idempotent: If true, prevents duplicate sessions.
        unlisted: If true, hides session from listings.
    """
    api_key = get_api_key()

    body: dict[str, Any] = {"prompt": prompt}
    if title is not None:
        body["title"] = title
    if snapshot_id is not None:
        body["snapshot_id"] = snapshot_id
    if playbook_id is not None:
        body["playbook_id"] = playbook_id
    if tags is not None:
        body["tags"] = tags
    if max_acu_limit is not None:
        body["max_acu_limit"] = max_acu_limit
    if idempotent:
        body["idempotent"] = idempotent
    if unlisted:
        body["unlisted"] = unlisted

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{DEVIN_API_BASE}/sessions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )

        if response.status_code == 401:
            raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")
        elif response.status_code == 422:
            raise ToolError(f"Validation error: {response.text}")
        elif response.status_code != 200:
            raise ToolError(
                f"Devin API error (status {response.status_code}): {response.text}"
            )

        return response.json()


if __name__ == "__main__":
    mcp.run()
