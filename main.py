import asyncio
import os
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.dependencies import Progress
from fastmcp.exceptions import ToolError

DEVIN_API_BASE = "https://api.devin.ai/v1"
POLL_INTERVAL_SECONDS = 10
TERMINAL_STATES = {"finished", "blocked", "expired"}

mcp = FastMCP("Devin Session Server")


def get_api_key() -> str:
    api_key = os.environ.get("DEVIN_API_KEY")
    if not api_key:
        raise ToolError(
            "DEVIN_API_KEY environment variable is not set. "
            "Please set it to your Devin API key (starts with 'apk_')."
        )
    return api_key


@mcp.tool(task=True)
async def delegate(
    prompt: str,
    title: str | None = None,
    snapshot_id: str | None = None,
    playbook_id: str | None = None,
    tags: list[str] | None = None,
    max_acu_limit: int | None = None,
    idempotent: bool = False,
    unlisted: bool = False,
    knowledge_ids: list[str] | None = None,
    secret_ids: list[str] | None = None,
    progress: Progress = Progress(),
) -> dict[str, Any]:
    """Delegate a task to Devin and monitor until completion.

    Creates a new Devin session with the given prompt and monitors it until
    the session reaches a terminal state (finished, blocked, or expired).
    Progress updates are reported as the session executes.

    Args:
        prompt: The instruction for Devin to execute.
        title: Custom session name. Auto-generated if not provided.
        snapshot_id: Restore from a previous snapshot.
        playbook_id: Associated playbook identifier.
        tags: Session categorization labels.
        max_acu_limit: Resource consumption ceiling (positive integer).
        idempotent: If true, prevents duplicate sessions with same prompt.
        unlisted: If true, hides session from listings.
        knowledge_ids: Knowledge bases to include. None uses all, empty list uses none.
        secret_ids: Secrets to include. None uses all, empty list uses none.
        progress: FastMCP Progress dependency for reporting status updates.

    Returns:
        Final session details including status, messages, and metadata.
    """
    api_key = get_api_key()

    await progress.set_message("Creating Devin session...")

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
    if knowledge_ids is not None:
        body["knowledge_ids"] = knowledge_ids
    if secret_ids is not None:
        body["secret_ids"] = secret_ids

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

        create_result = response.json()
        session_id = create_result["session_id"]

        await progress.set_message(f"Session created: {session_id}")

        last_message_count = 0
        last_status: str | None = None

        while True:
            response = await client.get(
                f"{DEVIN_API_BASE}/sessions/{session_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )

            if response.status_code == 404:
                raise ToolError(f"Session '{session_id}' not found during monitoring.")
            elif response.status_code == 401:
                raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")
            elif response.status_code != 200:
                raise ToolError(
                    f"Devin API error (status {response.status_code}): {response.text}"
                )

            session_data = response.json()
            current_status = session_data.get("status_enum", "unknown")
            messages = session_data.get("messages", [])

            if current_status != last_status:
                await progress.set_message(f"Status: {current_status}")
                last_status = current_status

            if len(messages) > last_message_count:
                new_messages = messages[last_message_count:]
                for msg in new_messages:
                    msg_type = msg.get("type", "message")
                    msg_content = msg.get("message", "")
                    display = (
                        msg_content[:200] + "..."
                        if len(msg_content) > 200
                        else msg_content
                    )
                    await progress.set_message(f"[{msg_type}] {display}")
                last_message_count = len(messages)

            if current_status in TERMINAL_STATES:
                await progress.set_message(f"Session {current_status}")
                return session_data

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
