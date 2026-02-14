import asyncio
import os
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.dependencies import Progress
from fastmcp.exceptions import ToolError
from mule import retry
from mule.stop_conditions import AttemptsExhausted, NoException

DEVIN_API_BASE = "https://api.devin.ai/v1"
POLL_INTERVAL_SECONDS = 10
TERMINAL_STATES = {"finished", "blocked", "expired"}
SLEEPING_STATES = {"suspended", "sleeping", "suspend_requested"}
MAX_POLL_RETRIES = 3
MONITORING_FAILED_MESSAGE = (
    "Session was created successfully. Check Devin dashboard for status."
)


def exponential_backoff(prev_state: Any, next_state: Any) -> int:
    """Exponential backoff: 2s, 4s, 8s, capped at 60s."""
    return min(2**next_state.attempt, 60)


mcp = FastMCP("Devin Session Server")


def get_api_key() -> str:
    api_key = os.environ.get("DEVIN_API_KEY")
    if not api_key:
        raise ToolError(
            "DEVIN_API_KEY environment variable is not set. "
            "Please set it to your Devin API key (starts with 'apk_')."
        )
    return api_key


def _build_request_body(
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
) -> dict[str, Any]:
    """Build the request body for session creation."""
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
    return body


async def _create_session(
    client: httpx.AsyncClient,
    api_key: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Create a new Devin session."""
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


class _PollResult:
    """Result of a single poll attempt."""

    def __init__(
        self,
        response: httpx.Response | None = None,
        error: str | None = None,
        retryable: bool = False,
    ):
        self.response = response
        self.error = error
        self.retryable = retryable


async def _poll_session_once(
    client: httpx.AsyncClient,
    session_id: str,
    api_key: str,
) -> _PollResult:
    """Execute a single poll request and categorize the result."""
    response = await client.get(
        f"{DEVIN_API_BASE}/sessions/{session_id}",
        headers={"Authorization": f"Bearer {api_key}"},
    )

    # Non-retryable errors - raise immediately
    if response.status_code == 404:
        raise ToolError(f"Session '{session_id}' not found during monitoring.")
    if response.status_code == 401:
        raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")

    # Retryable server errors
    if response.status_code in {500, 502, 503, 504}:
        return _PollResult(
            error=f"HTTP {response.status_code}",
            retryable=True,
        )

    # Other non-200 - non-retryable error
    if response.status_code != 200:
        return _PollResult(
            error=f"Unexpected status {response.status_code}: {response.text}",
            retryable=False,
        )

    # Success
    return _PollResult(response=response)


@retry(
    until=AttemptsExhausted(MAX_POLL_RETRIES) | NoException(),
    wait=exponential_backoff,  # type: ignore[arg-type]
)
async def _poll_session(
    client: httpx.AsyncClient,
    session_id: str,
    api_key: str,
    progress: Progress,
) -> _PollResult:
    """Poll session status with automatic retry on server errors."""
    result = await _poll_session_once(client, session_id, api_key)

    if result.retryable:
        await progress.set_message(f"Polling error ({result.error}), retrying...")
        raise Exception(result.error)

    return result


async def _report_progress(
    progress: Progress,
    session_data: dict[str, Any],
    last_status: str | None,
    last_message_count: int,
) -> tuple[str | None, int]:
    """Report status changes and new messages. Returns updated state."""
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
                msg_content[:200] + "..." if len(msg_content) > 200 else msg_content
            )
            await progress.set_message(f"[{msg_type}] {display}")
        last_message_count = len(messages)

    return last_status, last_message_count


async def _monitor_session(
    client: httpx.AsyncClient,
    session_id: str,
    session_url: str | None,
    api_key: str,
    progress: Progress,
) -> dict[str, Any]:
    """Monitor session until terminal state, with retry handling."""
    last_message_count = 0
    last_status: str | None = None

    while True:
        try:
            result = await _poll_session(client, session_id, api_key, progress)
        except ToolError:
            # Re-raise ToolErrors (401, 404) as-is
            raise
        except Exception:
            # Retries exhausted - return monitoring_failed
            return {
                "status": "monitoring_failed",
                "session_id": session_id,
                "url": session_url,
                "error": f"Polling failed after {MAX_POLL_RETRIES} retries",
                "message": MONITORING_FAILED_MESSAGE,
            }

        # Handle non-retryable errors (unexpected status codes)
        if result.error:
            return {
                "status": "monitoring_failed",
                "session_id": session_id,
                "url": session_url,
                "error": result.error,
                "message": MONITORING_FAILED_MESSAGE,
            }

        assert result.response is not None
        session_data = result.response.json()

        last_status, last_message_count = await _report_progress(
            progress, session_data, last_status, last_message_count
        )

        current_status = session_data.get("status_enum", "unknown")
        if current_status in TERMINAL_STATES:
            await progress.set_message(f"Session {current_status}")
            return session_data

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


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

    body = _build_request_body(
        prompt=prompt,
        title=title,
        snapshot_id=snapshot_id,
        playbook_id=playbook_id,
        tags=tags,
        max_acu_limit=max_acu_limit,
        idempotent=idempotent,
        unlisted=unlisted,
        knowledge_ids=knowledge_ids,
        secret_ids=secret_ids,
    )

    async with httpx.AsyncClient() as client:
        create_result = await _create_session(client, api_key, body)
        session_id = create_result["session_id"]
        session_url = create_result.get("url")

        await progress.set_message(f"Session created: {session_id}")

        return await _monitor_session(
            client, session_id, session_url, api_key, progress
        )


@mcp.tool()
async def get_session(session_id: str) -> dict[str, Any]:
    """Retrieve details about an existing Devin session.

    Use this to inspect the current status, messages, and metadata of a session.
    This is useful for checking whether a session is still running, has finished,
    or has gone to sleep due to ACU limits.

    Args:
        session_id: The identifier of the session to retrieve.

    Returns:
        Session details including status_enum, messages, title, tags, and metadata.
        The status_enum field indicates the session state: working, blocked, expired,
        finished, suspend_requested, resume_requested, or resumed.
    """
    api_key = get_api_key()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DEVIN_API_BASE}/sessions/{session_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )

        if response.status_code == 401:
            raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")
        elif response.status_code == 404:
            raise ToolError(f"Session '{session_id}' not found.")
        elif response.status_code != 200:
            raise ToolError(
                f"Devin API error (status {response.status_code}): {response.text}"
            )

        return response.json()


@mcp.tool()
async def list_sessions(
    limit: int = 100,
    offset: int = 0,
    tags: list[str] | None = None,
    user_email: str | None = None,
) -> dict[str, Any]:
    """List Devin sessions with optional filtering.

    Use this to find sessions by tags or email, or to get an overview of recent
    sessions. Useful for finding session IDs to inspect or resume.

    Args:
        limit: Maximum number of sessions to return (default 100).
        offset: Pagination offset (default 0).
        tags: Filter sessions by these tags.
        user_email: Filter sessions by the creator's email address.

    Returns:
        A dict with a 'sessions' key containing a list of session summaries,
        each with session_id, status_enum, title, tags, and other metadata.
    """
    api_key = get_api_key()

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if tags is not None:
        params["tags"] = tags
    if user_email is not None:
        params["user_email"] = user_email

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DEVIN_API_BASE}/sessions",
            headers={"Authorization": f"Bearer {api_key}"},
            params=params,
        )

        if response.status_code == 401:
            raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")
        elif response.status_code != 200:
            raise ToolError(
                f"Devin API error (status {response.status_code}): {response.text}"
            )

        return response.json()


@mcp.tool(task=True)
async def resume_session(
    session_id: str,
    message: str,
    progress: Progress = Progress(),
) -> dict[str, Any]:
    """Send a message to a Devin session and monitor it until completion.

    Use this to resume a session that has gone to sleep (e.g. due to ACU limits)
    or to send follow-up instructions to a running session. Sending a message to
    a sleeping session will wake it up. After sending the message, the session is
    monitored until it reaches a terminal state.

    Args:
        session_id: The identifier of the session to message.
        message: The message to send (e.g. instructions to continue work).
        progress: FastMCP Progress dependency for reporting status updates.

    Returns:
        Final session details including status, messages, and metadata.
    """
    api_key = get_api_key()

    async with httpx.AsyncClient() as client:
        # First, check current session status
        await progress.set_message(f"Checking session {session_id}...")
        status_response = await client.get(
            f"{DEVIN_API_BASE}/sessions/{session_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )

        if status_response.status_code == 401:
            raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")
        elif status_response.status_code == 404:
            raise ToolError(f"Session '{session_id}' not found.")
        elif status_response.status_code != 200:
            raise ToolError(
                f"Devin API error (status {status_response.status_code}): "
                f"{status_response.text}"
            )

        session_data = status_response.json()
        current_status = session_data.get("status_enum", "unknown")

        if current_status in TERMINAL_STATES:
            raise ToolError(
                f"Session '{session_id}' is in terminal state '{current_status}' "
                f"and cannot receive messages."
            )

        # Send the message
        await progress.set_message("Sending message...")
        msg_response = await client.post(
            f"{DEVIN_API_BASE}/sessions/{session_id}/message",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"message": message},
        )

        if msg_response.status_code == 401:
            raise ToolError("Invalid API key. Please check your DEVIN_API_KEY.")
        elif msg_response.status_code == 404:
            raise ToolError(f"Session '{session_id}' not found.")
        elif msg_response.status_code != 200:
            raise ToolError(
                f"Failed to send message (status {msg_response.status_code}): "
                f"{msg_response.text}"
            )

        session_url = session_data.get("url")
        await progress.set_message("Message sent. Monitoring session...")

        return await _monitor_session(
            client, session_id, session_url, api_key, progress
        )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
