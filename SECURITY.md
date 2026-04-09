# Security Policy

## Threat model

agent-container runs autonomous AI coding agents inside ephemeral Daytona workspaces. The
primary threat vectors are:

- **Secret leakage** — API keys, tokens, or credentials committed to the repo or logged
- **Workspace escape** — agent code executing outside the Daytona workspace boundary
- **Prompt injection** — malicious repo content manipulating the agent's instructions
- **Dependency compromise** — supply chain attack via a compromised Python package
- **Inference traffic interception** — model prompts (which contain code context) captured in transit

## What we do about each

### Secret leakage
- `detect-secrets` pre-commit hook blocks commits containing potential secrets
- `.gitignore` excludes all `.env` variants
- All credentials passed as Daytona workspace env vars, never written to files inside workspaces
- GitHub secret scanning enabled on this repo

### Workspace isolation
- Each agent run gets a fresh, isolated Daytona workspace (Docker container)
- Workspaces are destroyed immediately after the run completes or fails (`try/finally`)
- No shared filesystem between workspaces
- Workspaces run as non-root by default

### Prompt injection
- The task prompt is the only user-controlled input to the agent
- Repo content is not passed as part of the system prompt — the agent reads files directly
- Mitigation is architectural: the workspace is ephemeral, so a successful injection can only
  affect that run's output (a diff/PR), not persistent system state

### Dependency security
- `pip-audit` runs in CI on every push to check for known CVEs in dependencies
- Dependabot opens PRs weekly for dependency updates
- Direct dependencies are pinned to minor versions in `pyproject.toml`

### Inference traffic
- Self-hosted SGLang/Ollama deployments keep all traffic on the LAN — no external calls
- Cloud API usage (Anthropic, Together.ai) should be behind HTTPS — never plain HTTP
- `OPENAI_BASE_URL` must use HTTPS in production if pointing to a remote host

## Supported versions

| Version | Supported |
|---|---|
| `main` branch | Yes |
| Tagged releases | Yes |
| Older branches | No |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report privately via GitHub's security advisory feature:
**Security → Report a vulnerability** on this repo.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

We will respond within 5 business days and aim to publish a fix within 30 days of confirmation.
