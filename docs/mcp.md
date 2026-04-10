# MCP Integration

The sandbox exposes an [MCP](https://modelcontextprotocol.io) server so you can trigger runs
directly from Claude Code or Gemini CLI sessions — without leaving your editor.

## Setup

=== "Claude Code"

    Add to `.claude/settings.json` in your repo (checked in — team gets this automatically):

    ```json
    {
      "mcpServers": {
        "agent-container": {
          "command": "python",
          "args": ["-m", "mcp.server"]
        }
      }
    }
    ```

=== "Gemini CLI"

    Add to `~/.gemini/settings.json`:

    ```json
    {
      "mcpServers": {
        "agent-container": {
          "command": "python",
          "args": ["-m", "mcp.server"]
        }
      }
    }
    ```

## Usage

Once configured, you can trigger runs conversationally:

```
You:     "Fix the pagination bug in org/myapp"

Claude:  I'll run that in the agent sandbox.
         [calls sandbox_run]

         Done. PR #42 opened on branch agent/fix-pagination-20260410.
         +12 −3 in api/pagination.py. Tests: 24 passed.

         Want me to review the diff before it merges?
```

## Available tools

| Tool | Description |
|---|---|
| `sandbox_run` | Start an agent task. Returns run ID immediately. |
| `sandbox_status` | Get the current status of a run. |
| `sandbox_list` | List all recent runs with status and PR links. |
| `sandbox_stop` | Stop a running task and destroy its sandbox. |

### `sandbox_run` parameters

```json
{
  "repo": "https://github.com/org/myapp",
  "task": "Fix the off-by-one in paginate()",
  "backend": "opencode",
  "branch": "main",
  "create_pr": true,
  "timeout_seconds": 300
}
```

## Without MCP

Both CLIs can trigger runs via their Bash tool without any MCP setup:

```bash
# inside a Claude Code session
$ agent-run --repo https://github.com/org/myapp --task "Fix the login bug"
```

MCP is the upgrade: structured typed output, progress streaming, and no raw terminal dump in
the conversation.
