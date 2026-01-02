# devin-mcp

MCP server for delegating tasks to Devin AI.

## Features

- **delegate** - Create a Devin session and monitor it until completion
  - Runs as a background task
  - Reports progress updates (status changes, messages)
  - Supports all Devin session options (snapshots, playbooks, tags, etc.)

## Requirements

- Python 3.14+
- Devin API key (starts with `apk_`)

## Usage

### Claude Code

Add to your Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "devin": {
      "command": "uv",
      "args": ["run", "python", "main.py"],
      "cwd": "/path/to/devin-mcp",
      "env": {
        "DEVIN_API_KEY": "apk_your_key_here"
      }
    }
  }
}
```

Or add it via the CLI:

```bash
claude mcp add devin -e DEVIN_API_KEY=apk_your_key_here -- uv run python main.py
```

### Standalone

Run the MCP server directly:

```bash
uv run python main.py
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
