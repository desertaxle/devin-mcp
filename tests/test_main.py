import runpy
from unittest.mock import AsyncMock, patch

import pytest
import respx
from fastmcp.exceptions import ResourceError, ToolError

from main import (
    DEVIN_API_BASE,
    MAX_POLL_RETRIES,
    delegate,
    exponential_backoff,
    get_api_key,
    get_playbook,
    get_session,
    list_playbooks,
    list_sessions,
    resume_session,
)


class TestGetApiKey:
    def test_returns_api_key_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVIN_API_KEY", "apk_test123")
        assert get_api_key() == "apk_test123"

    def test_raises_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)
        with pytest.raises(ToolError, match="DEVIN_API_KEY"):
            get_api_key()

    def test_raises_custom_error_class(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)
        with pytest.raises(ResourceError, match="DEVIN_API_KEY"):
            get_api_key(ResourceError)


class TestExponentialBackoff:
    def test_returns_exponential_values_capped_at_60(self) -> None:
        class MockState:
            def __init__(self, attempt: int) -> None:
                self.attempt = attempt

        # 2^1 = 2, 2^2 = 4, 2^3 = 8, 2^6 = 64 -> capped to 60
        assert exponential_backoff(None, MockState(1)) == 2
        assert exponential_backoff(None, MockState(2)) == 4
        assert exponential_backoff(None, MockState(3)) == 8
        assert exponential_backoff(None, MockState(6)) == 60  # capped
        assert exponential_backoff(None, MockState(10)) == 60  # capped


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

    @pytest.fixture(autouse=True)
    def fast_retry_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set retry backoff to instant for faster tests."""
        monkeypatch.setattr("main.exponential_backoff", lambda prev, next: 0)

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
    async def test_monitor_500_retries_exhausted_returns_session_info(
        self, mock_progress: AsyncMock
    ) -> None:
        """After retries exhausted on 500, return session info."""
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "https://app.devin.ai/sess_123"}
        )
        # Return 500 for all retry attempts
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            500, text="Internal Server Error"
        )

        result = await delegate("Test prompt", progress=mock_progress)

        assert result["status"] == "monitoring_failed"
        assert result["session_id"] == "sess_123"
        assert result["url"] == "https://app.devin.ai/sess_123"
        assert f"after {MAX_POLL_RETRIES} retries" in result["error"]
        assert "created successfully" in result["message"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_poll_retries_on_500_then_succeeds(
        self, mock_progress: AsyncMock
    ) -> None:
        """500 errors should retry and succeed if subsequent attempt works."""
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "https://app.devin.ai/sess_123"}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").mock(
            side_effect=[
                respx.MockResponse(500, text="Internal Server Error"),
                respx.MockResponse(500, text="Internal Server Error"),
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

        result = await delegate("Test prompt", progress=mock_progress)

        assert result["status_enum"] == "finished"
        mock_progress.set_message.assert_any_call(
            "Polling error (HTTP 500), retrying..."
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_poll_502_503_504_are_retryable(
        self, mock_progress: AsyncMock
    ) -> None:
        """502, 503, 504 errors should also be retryable."""
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "https://app.devin.ai/sess_123"}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").mock(
            side_effect=[
                respx.MockResponse(502, text="Bad Gateway"),
                respx.MockResponse(503, text="Service Unavailable"),
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

        result = await delegate("Test prompt", progress=mock_progress)

        assert result["status_enum"] == "finished"
        mock_progress.set_message.assert_any_call(
            "Polling error (HTTP 502), retrying..."
        )
        mock_progress.set_message.assert_any_call(
            "Polling error (HTTP 503), retrying..."
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_poll_unexpected_status_returns_session_info(
        self, mock_progress: AsyncMock
    ) -> None:
        """Non-retryable, non-error status codes should return session info."""
        respx.post(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"session_id": "sess_123", "url": "https://app.devin.ai/sess_123"}
        )
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            418, text="I'm a teapot"
        )

        result = await delegate("Test prompt", progress=mock_progress)

        assert result["status"] == "monitoring_failed"
        assert result["session_id"] == "sess_123"
        assert "418" in result["error"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_api_key_missing_raises_tool_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_progress: AsyncMock
    ) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)

        with pytest.raises(ToolError, match="DEVIN_API_KEY"):
            await delegate("Test prompt", progress=mock_progress)


class TestGetSession:
    @pytest.fixture(autouse=True)
    def set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVIN_API_KEY", "apk_test123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_session_details(self) -> None:
        session_data = {
            "session_id": "sess_456",
            "status_enum": "working",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T01:00:00Z",
            "messages": [{"type": "user_message", "message": "Hello"}],
            "title": "My session",
            "tags": ["test"],
        }
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_456").respond(200, json=session_data)

        result = await get_session("sess_456")

        assert result["session_id"] == "sess_456"
        assert result["status_enum"] == "working"
        assert result["title"] == "My session"
        assert len(result["messages"]) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_raises_tool_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_456").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ToolError, match="Invalid API key"):
            await get_session("sess_456")

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_raises_tool_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_456").respond(
            404, json={"error": "Not found"}
        )

        with pytest.raises(ToolError, match="not found"):
            await get_session("sess_456")

    @respx.mock
    @pytest.mark.asyncio
    async def test_500_raises_tool_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_456").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ToolError, match="Devin API error.*500"):
            await get_session("sess_456")

    @pytest.mark.asyncio
    async def test_api_key_missing_raises_tool_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)

        with pytest.raises(ToolError, match="DEVIN_API_KEY"):
            await get_session("sess_456")


class TestListSessions:
    @pytest.fixture(autouse=True)
    def set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVIN_API_KEY", "apk_test123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_sessions_list(self) -> None:
        response_data = {
            "sessions": [
                {
                    "session_id": "sess_1",
                    "status": "running",
                    "status_enum": "working",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T01:00:00Z",
                },
                {
                    "session_id": "sess_2",
                    "status": "completed",
                    "status_enum": "finished",
                    "created_at": "2025-01-02T00:00:00Z",
                    "updated_at": "2025-01-02T01:00:00Z",
                },
            ]
        }
        respx.get(f"{DEVIN_API_BASE}/sessions").respond(200, json=response_data)

        result = await list_sessions()

        assert len(result["sessions"]) == 2
        assert result["sessions"][0]["session_id"] == "sess_1"
        assert result["sessions"][1]["session_id"] == "sess_2"

    @respx.mock
    @pytest.mark.asyncio
    async def test_passes_query_params(self) -> None:
        route = respx.get(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"sessions": []}
        )

        await list_sessions(limit=10, offset=5, tags=["ci"], user_email="a@b.com")

        request = route.calls[0].request
        assert "limit=10" in str(request.url)
        assert "offset=5" in str(request.url)
        assert "tags=ci" in str(request.url)
        assert "user_email=a%40b.com" in str(request.url)

    @respx.mock
    @pytest.mark.asyncio
    async def test_omits_none_params(self) -> None:
        route = respx.get(f"{DEVIN_API_BASE}/sessions").respond(
            200, json={"sessions": []}
        )

        await list_sessions()

        request = route.calls[0].request
        assert "tags" not in str(request.url)
        assert "user_email" not in str(request.url)

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_raises_tool_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ToolError, match="Invalid API key"):
            await list_sessions()

    @respx.mock
    @pytest.mark.asyncio
    async def test_500_raises_tool_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ToolError, match="Devin API error.*500"):
            await list_sessions()

    @pytest.mark.asyncio
    async def test_api_key_missing_raises_tool_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)

        with pytest.raises(ToolError, match="DEVIN_API_KEY"):
            await list_sessions()


class TestResumeSession:
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
        monkeypatch.setattr("main.POLL_INTERVAL_SECONDS", 0)

    @pytest.fixture(autouse=True)
    def fast_retry_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("main.exponential_backoff", lambda prev, next: 0)

    @respx.mock
    @pytest.mark.asyncio
    async def test_sends_message_and_monitors_to_completion(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").mock(
            side_effect=[
                # First call: status check
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "suspended",
                        "url": "https://app.devin.ai/sessions/sess_123",
                    },
                ),
                # Second call: monitoring poll
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
        respx.post(f"{DEVIN_API_BASE}/sessions/sess_123/message").respond(
            200, json=None
        )

        result = await resume_session(
            "sess_123", "Continue working", progress=mock_progress
        )

        assert result["status_enum"] == "finished"
        mock_progress.set_message.assert_any_call("Checking session sess_123...")
        mock_progress.set_message.assert_any_call("Sending message...")
        mock_progress.set_message.assert_any_call("Message sent. Monitoring session...")

    @respx.mock
    @pytest.mark.asyncio
    async def test_resumes_working_session(self, mock_progress: AsyncMock) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").mock(
            side_effect=[
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "working",
                        "url": "https://app.devin.ai/sessions/sess_123",
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
        respx.post(f"{DEVIN_API_BASE}/sessions/sess_123/message").respond(
            200, json=None
        )

        result = await resume_session("sess_123", "Keep going", progress=mock_progress)

        assert result["status_enum"] == "finished"

    @respx.mock
    @pytest.mark.asyncio
    async def test_rejects_terminal_state_session(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={
                "session_id": "sess_123",
                "status_enum": "finished",
            },
        )

        with pytest.raises(ToolError, match="terminal state"):
            await resume_session("sess_123", "Continue", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_status_check_404_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            404, json={"error": "Not found"}
        )

        with pytest.raises(ToolError, match="not found"):
            await resume_session("sess_123", "Continue", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_status_check_401_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ToolError, match="Invalid API key"):
            await resume_session("sess_123", "Continue", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_status_check_500_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ToolError, match="Devin API error.*500"):
            await resume_session("sess_123", "Continue", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_message_401_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={"session_id": "sess_123", "status_enum": "working"},
        )
        respx.post(f"{DEVIN_API_BASE}/sessions/sess_123/message").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ToolError, match="Invalid API key"):
            await resume_session("sess_123", "Continue", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_message_404_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={"session_id": "sess_123", "status_enum": "working"},
        )
        respx.post(f"{DEVIN_API_BASE}/sessions/sess_123/message").respond(
            404, json={"error": "Not found"}
        )

        with pytest.raises(ToolError, match="not found"):
            await resume_session("sess_123", "Continue", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_send_message_500_raises_tool_error(
        self, mock_progress: AsyncMock
    ) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").respond(
            200,
            json={"session_id": "sess_123", "status_enum": "working"},
        )
        respx.post(f"{DEVIN_API_BASE}/sessions/sess_123/message").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ToolError, match="Failed to send message.*500"):
            await resume_session("sess_123", "Continue", progress=mock_progress)

    @respx.mock
    @pytest.mark.asyncio
    async def test_sends_correct_message_body(self, mock_progress: AsyncMock) -> None:
        respx.get(f"{DEVIN_API_BASE}/sessions/sess_123").mock(
            side_effect=[
                respx.MockResponse(
                    200,
                    json={
                        "session_id": "sess_123",
                        "status_enum": "working",
                        "url": "https://app.devin.ai/sessions/sess_123",
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
        msg_route = respx.post(f"{DEVIN_API_BASE}/sessions/sess_123/message").respond(
            200, json=None
        )

        await resume_session(
            "sess_123", "Please continue with the task", progress=mock_progress
        )

        import json

        body = json.loads(msg_route.calls[0].request.content)
        assert body["message"] == "Please continue with the task"

    @pytest.mark.asyncio
    async def test_api_key_missing_raises_tool_error(
        self, monkeypatch: pytest.MonkeyPatch, mock_progress: AsyncMock
    ) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)

        with pytest.raises(ToolError, match="DEVIN_API_KEY"):
            await resume_session("sess_123", "Continue", progress=mock_progress)


class TestListPlaybooks:
    @pytest.fixture(autouse=True)
    def set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVIN_API_KEY", "apk_test123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_playbooks_json(self) -> None:
        playbooks_data = [
            {
                "playbook_id": "play_1",
                "title": "Deploy to staging",
                "body": "Steps to deploy",
                "status": "active",
            },
            {
                "playbook_id": "play_2",
                "title": "Run tests",
                "body": "Steps to run tests",
                "status": "active",
            },
        ]
        respx.get(f"{DEVIN_API_BASE}/playbooks").respond(200, json=playbooks_data)

        result = await list_playbooks()

        import json

        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["playbook_id"] == "play_1"
        assert parsed[1]["title"] == "Run tests"

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_raises_resource_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/playbooks").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ResourceError, match="Invalid API key"):
            await list_playbooks()

    @respx.mock
    @pytest.mark.asyncio
    async def test_500_raises_resource_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/playbooks").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ResourceError, match="Devin API error.*500"):
            await list_playbooks()

    @pytest.mark.asyncio
    async def test_api_key_missing_raises_resource_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)

        with pytest.raises(ResourceError, match="DEVIN_API_KEY"):
            await list_playbooks()


class TestGetPlaybook:
    @pytest.fixture(autouse=True)
    def set_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVIN_API_KEY", "apk_test123")

    @respx.mock
    @pytest.mark.asyncio
    async def test_returns_playbook_json(self) -> None:
        playbook_data = {
            "playbook_id": "play_1",
            "title": "Deploy to staging",
            "body": "Steps to deploy",
            "status": "active",
        }
        respx.get(f"{DEVIN_API_BASE}/playbooks/play_1").respond(200, json=playbook_data)

        result = await get_playbook("play_1")

        import json

        parsed = json.loads(result)
        assert parsed["playbook_id"] == "play_1"
        assert parsed["title"] == "Deploy to staging"

    @respx.mock
    @pytest.mark.asyncio
    async def test_401_raises_resource_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/playbooks/play_1").respond(
            401, json={"error": "Unauthorized"}
        )

        with pytest.raises(ResourceError, match="Invalid API key"):
            await get_playbook("play_1")

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_raises_resource_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/playbooks/play_1").respond(
            404, json={"error": "Not found"}
        )

        with pytest.raises(ResourceError, match="not found"):
            await get_playbook("play_1")

    @respx.mock
    @pytest.mark.asyncio
    async def test_500_raises_resource_error(self) -> None:
        respx.get(f"{DEVIN_API_BASE}/playbooks/play_1").respond(
            500, text="Internal Server Error"
        )

        with pytest.raises(ResourceError, match="Devin API error.*500"):
            await get_playbook("play_1")

    @pytest.mark.asyncio
    async def test_api_key_missing_raises_resource_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DEVIN_API_KEY", raising=False)

        with pytest.raises(ResourceError, match="DEVIN_API_KEY"):
            await get_playbook("play_1")


class TestMain:
    def test_main_runs_mcp_server(self) -> None:
        with patch("fastmcp.FastMCP.run") as mock_run:
            runpy.run_module("main", run_name="__main__", alter_sys=True)
            mock_run.assert_called_once()
