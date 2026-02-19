# devin-mcp

MCP server for creating, monitoring, and managing Devin AI sessions.

## Tools

### delegate

Create a Devin session and monitor it until completion. Runs as a background task with live progress updates (status changes, messages).

Supports all Devin session options:

| Parameter | Description |
|---|---|
| `prompt` | The instruction for Devin to execute (required) |
| `title` | Custom session name (auto-generated if omitted) |
| `snapshot_id` | Restore from a previous snapshot |
| `playbook_id` | Associated playbook identifier |
| `tags` | Session categorization labels |
| `max_acu_limit` | Resource consumption ceiling |
| `idempotent` | Prevent duplicate sessions with the same prompt |
| `unlisted` | Hide session from listings |
| `knowledge_ids` | Knowledge bases to include (`None` uses all, `[]` uses none) |
| `secret_ids` | Secrets to include (`None` uses all, `[]` uses none) |

### get_session

Retrieve details about an existing Devin session, including its status, messages, and metadata.

| Parameter | Description |
|---|---|
| `session_id` | The identifier of the session to retrieve (required) |

### list_sessions

List Devin sessions with optional filtering. Useful for finding session IDs to inspect or resume.

| Parameter | Description |
|---|---|
| `limit` | Maximum number of sessions to return (default 100) |
| `offset` | Pagination offset (default 0) |
| `tags` | Filter sessions by tags |
| `user_email` | Filter sessions by creator's email |

### resume_session

Send a message to an existing Devin session and monitor it until completion. Runs as a background task. Use this to wake a sleeping session or send follow-up instructions to a running one.

| Parameter | Description |
|---|---|
| `session_id` | The identifier of the session to message (required) |
| `message` | The message to send (required) |

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
