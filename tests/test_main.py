import runpy
from unittest.mock import AsyncMock, patch

import pytest
import respx
from fastmcp.exceptions import ToolError

from main import DEVIN_API_BASE, get_api_key
from main import delegate as delegate_tool

# Access the underlying function from the FastMCP tool wrapper
delegate = delegate_tool.fn


class TestGetApiKey:
    def test_returns_api_key_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVIN_API_KEY", "apk_test123")
        assert get_api_key() == "apk_test123"

    def test_raises_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)
        with pytest.raises(ToolError, match="DEVIN_API_KEY"):
            get_api_key()


class TestDelegate:
    @pytest.fixture
    def mock_progress(self) -> AsyncMock:
        progress = AsyncMock()
        progress.set_message = AsyncMock()
        return progress

    @pytest.fixture(autouse=True)
    def set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVIN_API_KEY", "apk_test123")

    @pytest.fixture(autouse=True)
    def fast_polling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set poll interval to 0 for faster tests."""
        monkeypatch.setattr("main.POLL_INTERVAL_SECONDS", 0)

    @respx.mock
    @pytest.mark.asyncio
    async def test_creates_session_and_monitors_to_completion(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200,
            json={
                "session_id": "sess_123",
                "url": "https://app.devin.ai/sessions/sess_123",
            },
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={"session_id": "sess_123", "status_enum": "finished", "messages": []},
        )

        result = await delegate("Test prompt", progress=mock_progress)

        assert result["status_enum"] == "finished"
        assert result["session_id"] == "sess_123"
        mock_progress.set_message.assert_any_call("Creating Devin session...")
        mock_progress.set_message.assert_any_call("Session created: sess_123")
        mock_progress.set_message.assert_any_call("Session finished")

    @respx.mock
    @pytest.mark.asyncio
    async def test_creates_session_with_all_optional_params(
        self, mock_progress: AsyncMock
    ) -> None:
        create_route = respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={"session_id": "sess_123", "status_enum": "finished", "messages": []},
        )

        await delegate(
            prompt="Test prompt",
            title="My Session",
            snapshot_id="snap_123",
            playbook_id="play_123",
            tags=["test", "ci"],
            max_acu_limit=100,
            idempotent=True,
            unlisted=True,
            knowledge_ids=["know_1", "know_2"],
            secret_ids=["sec_1"],
            progress=mock_progress,
        )

        request_body = create_route.calls[0].request.content
        import json

        body = json.loads(request_body)

        assert body["prompt"] == "Test prompt"
        assert body["title"] == "My Session"
        assert body["snapshot_id"] == "snap_123"
        assert body["playbook_id"] == "play_123"
        assert body["tags"] == ["test", "ci"]
        assert body["max_acu_limit"] == 100
        assert body["idempotent"] is True
        assert body["unlisted"] is True
        assert body["knowledge_ids"] == ["know_1", "know_2"]
        assert body["secret_ids"] == ["sec_1"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_monitors_until_blocked(self, mock_progress: AsyncMock) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={"session_id": "sess_123", "status_enum": "blocked", "messages": []},
        )

        result = await delegate("Test prompt", progress=mock_progress)

        assert result["status_enum"] == "blocked"
        mock_progress.set_message.assert_any_call("Session blocked")

    @respx.mock
    @pytest.mark.asyncio
    async def test_monitors_until_expired(self, mock_progress: AsyncMock) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={"session_id": "sess_123", "status_enum": "expired", "messages": []},
        )

        result = await delegate("Test prompt", progress=mock_progress)

        assert result["status_enum"] == "expired"
        mock_progress.set_message.assert_any_call("Session expired")

    @respx.mock
    @pytest.mark.asyncio
    async def test_reports_status_changes(self, mock_progress: AsyncMock) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").mock(
            side_effect=[
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "working",
                        "messages": [],
                    },
                ),
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "working",
                        "messages": [],
                    },
                ),
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "finished",
                        "messages": [],
                    },
                ),
            ]
        )

        await delegate("Test prompt", progress=mock_progress)

        # Status should only be reported when it changes
        status_calls = [
            c for c in mock_progress.set_message.call_args_list if "Status:" in str(c)
        ]
        assert len(status_calls) == 2  # working, then finished
        mock_progress.set_message.assert_any_call("Status: working")
        mock_progress.set_message.assert_any_call("Status: finished")

    @respx.mock
    @pytest.mark.asyncio
    async def test_reports_new_messages(self, mock_progress: AsyncMock) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").mock(
            side_effect=[
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "working",
                        "messages": [{"type": "user_message", "message": "Hello"}],
                    },
                ),
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "finished",
                        "messages": [
                            {"type": "user_message", "message": "Hello"},
                            {"type": "devin_message", "message": "Hi there!"},
                        ],
                    },
                ),
            ]
        )

        await delegate("Test prompt", progress=mock_progress)

        mock_progress.set_message.assert_any_call("[user_message] Hello")
        mock_progress.set_message.assert_any_call("[devin_message] Hi there!")

    @respx.mock
    @pytest.mark.asyncio
    async def test_truncates_long_messages(self, mock_progress: AsyncMock) -> None:
        long_message = "A" * 300
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={
                "session_id": "sess_123",
                "status_enum": "finished",
                "messages": [{"type": "devin_message", "message": long_message}],
            },
        )

        await delegate("Test prompt", progress=mock_progress)

        expected_truncated = "A" * 200 + "..."
        mock_progress.set_message.assert_any_call(
            f"[devin_message] {expected_truncated}"
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_401_raises_tool_error(self, mock_progress: AsyncMock) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ToolError, match="Invalid API key"):
            await delegate("Test prompt", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_422_raises_tool_error(self, mock_progress: AsyncMock) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            422, json={"detail": "Validation failed"}
        )

        with pytest.raises(ToolError, match="Validation error"):
            await delegate("Test prompt", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_create_500_raises_tool_error(self, mock_progress: AsyncMock) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ToolError, match="Devin API error.*500"):
            await delegate("Test prompt", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_monitor_404_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            404, json={"error": "Not found"}
        )

        with pytest.raises(ToolError, match="not found during monitoring"):
            await delegate("Test prompt", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_monitor_401_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ToolError, match="Invalid API key"):
            await delegate("Test prompt", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_monitor_500_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "..."}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ToolError, match="Devin API error.*500"):
            await delegate("Test prompt", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_key_missing_raises_tool_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_progress: AsyncMock
    ) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)

        with pytest.raises(ToolError, match="DEVIN_API_KEY"):
            await delegate("Test prompt", progress=mock_progress)


class TestMain:
    def test_main_runs_mcp_server(self) -> None:
        with patch("fastmcp.FastMCP.run") as mock_run:
            runpy.run_module("main", run_name="__main__", alter_sys=True)
            mock_run.assert_called_once()
