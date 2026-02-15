# devin-mcp

MCP server for delegating tasks to Devin AI.

## Features

- **delegate** - Create a Devin session and monitor it until completion
  - Runs as a background task
  - Reports progress updates (status changes, messages)
  - Supports all Devin session options (snapshots, playbooks, tags, etc.)
- **get_session** - Retrieve details about an existing session
  - Check status, messages, and metadata
  - Useful for inspecting whether a session is running, finished, or sleeping
- **list_sessions** - List sessions with optional filtering
  - Filter by tags or creator email
  - Paginated results for finding session IDs to inspect or resume
- **resume_session** - Send a message to a session and monitor it until completion
  - Wakes up sessions that have gone to sleep (e.g. due to ACU limits)
  - Send follow-up instructions to running sessions
  - Runs as a background task with progress reporting

## Requirements

- Python 3.13+
- Devin API key (starts with `apk_`)

## Usage

### Claude Code

```bash
claude mcp add devin -e DEVIN_API_KEY=apk_your_key_here -- uvx --from git+https://github.com/desertaxle/devin-mcp devin-mcp
```

### Standalone

Run the MCP server directly:

```bash
uvx --from git+https://github.com/desertaxle/devin-mcp devin-mcp
```

## Development

Install dev dependencies:

```bash
uv sync
```

Run tests:

```bash
uv run pytest
```

Run linter, formatter, and type checker:

```bash
uv run prek run --all-files
```
