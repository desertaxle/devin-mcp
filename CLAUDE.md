# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run tests (enforces 100% coverage)
uv run pytest

# Run a single test
uv run pytest tests/test_main.py::TestDelegate::test_creates_session_and_monitors_to_completion

# Run linter, formatter, and type checker
uv run prek run --all-files
```

## Architecture

This is a single-file MCP server (`main.py`) that exposes one tool: `delegate`. The tool creates Devin AI sessions via the Devin REST API and polls until completion.

Key components in `main.py`:
- `mcp` - FastMCP server instance
- `delegate()` - Background task tool (decorated with `@mcp.tool(task=True)`) that creates and monitors Devin sessions
- `get_api_key()` - Reads `DEVIN_API_KEY` from environment

The delegate tool uses httpx for async HTTP calls and reports progress via FastMCP's `Progress` dependency. Sessions are polled every 10 seconds until reaching a terminal state (`finished`, `blocked`, or `expired`).

## Testing

Tests use respx to mock httpx requests. The `delegate` function is accessed via `delegate_tool.fn` to bypass the FastMCP wrapper. Tests set `POLL_INTERVAL_SECONDS` to 0 for speed.
