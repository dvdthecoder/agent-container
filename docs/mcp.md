# MCP Integration

The sandbox exposes an [MCP](https://modelcontextprotocol.io) server so you can trigger runs
directly from Claude Code or Gemini CLI sessions — without leaving your editor.

## Setup

Both `.claude/settings.json` and `.gemini/settings.json` are already checked into this repo.
Clone the repo, fill in your tokens, and the tools appear automatically.

=== "Claude Code"

    `.claude/settings.json` (already in the repo):

    ```json
    {
      "mcpServers": {
        "agent-container": {
          "command": "python3",
          "args": ["-m", "mcp_server.server"],
          "env": {
            "MODAL_TOKEN_ID": "",
            "MODAL_TOKEN_SECRET": "",
            "GITHUB_TOKEN": "",
            "OPENAI_BASE_URL": "",
            "OPENAI_API_KEY": ""
          }
        }
      }
    }
    ```

    Verify with:
    ```bash
    claude mcp list
    # agent-container   python3 -m mcp_server.server
    ```

=== "Gemini CLI"

    `.gemini/settings.json` (already in the repo):

    ```json
    {
      "mcpServers": {
        "agent-container": {
          "command": "python3",
          "args": ["-m", "mcp_server.server"],
          "env": {
            "MODAL_TOKEN_ID": "",
            "MODAL_TOKEN_SECRET": "",
            "GITHUB_TOKEN": "",
            "OPENAI_BASE_URL": "",
            "OPENAI_API_KEY": ""
          }
        }
      }
    }
    ```

## Start the server standalone

```bash
make mcp
# or: python3 -m mcp_server.server

# SSE mode (remote / multi-client)
python3 -m mcp_server.server --transport sse --port 8001
```

## Usage

Once configured, you can trigger runs conversationally:

```
You:     "Fix the pagination bug in org/myapp"

Claude:  I'll run that in the agent sandbox.
         [calls sandbox_run]

         Done. PR #42 opened on branch agent/opencode-20260424-120000.
         +12 −3 in api/pagination.py. Tests: 24 passed.

         Want me to review the diff before it merges?
```

## Available tools

| Tool | Description |
|---|---|
| `sandbox_run` | Boot a sandbox, run the agent, return the result. |
| `sandbox_status` | Get the current phase + event log for a run. |
| `sandbox_list` | List all runs (active, completed, failed) sorted newest-first. |
| `sandbox_stop` | Cancel a running task and mark it terminal. |

### `sandbox_run` parameters

```json
{
  "repo": "https://github.com/org/myapp",
  "task": "Fix the off-by-one in paginate()",
  "backend": "opencode",
  "base_branch": "main",
  "create_pr": true,
  "run_tests": true,
  "timeout_seconds": 300
}
```

## Without MCP

Both CLIs can trigger runs via their Bash tool without any MCP setup:

```bash
# inside a Claude Code or Gemini CLI session
agent-run --repo https://github.com/org/myapp --task "Fix the login bug"
```

MCP is the upgrade: structured typed output, progress notifications, and no raw terminal dump in
the conversation.
