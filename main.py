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


if __name__ == "__main__":
    mcp.run()
