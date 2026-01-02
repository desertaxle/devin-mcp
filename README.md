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
