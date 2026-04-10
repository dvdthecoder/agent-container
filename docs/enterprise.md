# Enterprise & GitLab

## GitLab support

Swap `GITHUB_TOKEN` for `GITLAB_TOKEN`. PRs become Merge Requests. Everything else is identical.

```bash
GITLAB_TOKEN=glpat-...
GITLAB_URL=https://gitlab.yourcompany.com   # omit for gitlab.com
```

The `git_ops` module uses `glab mr create` instead of `gh pr create` when a GitLab URL is detected.

### GitLab CI trigger

```yaml
# .gitlab-ci.yml
agent-fix:
  stage: maintenance
  rules:
    - when: manual
  script:
    - agent-run --task "$AGENT_TASK" --repo "$CI_PROJECT_URL"
  variables:
    MODAL_TOKEN_ID: $MODAL_TOKEN_ID
    MODAL_TOKEN_SECRET: $MODAL_TOKEN_SECRET
```

### Issue-triggered runs

Label a GitLab issue `agent-run`. A webhook fires, the sandbox picks it up, creates a branch, runs
the agent with the issue description as the task prompt, and opens an MR referencing the issue.

### Cross-repo changes at scale

Run the same task against multiple repos in parallel:

```python
from sandbox import ModalSandbox, SandboxConfig, AgentTaskSpec
from concurrent.futures import ThreadPoolExecutor

config = SandboxConfig.from_env()
repos = [
    "https://gitlab.company.com/team/service-a",
    "https://gitlab.company.com/team/service-b",
    "https://gitlab.company.com/team/service-c",
]

def run(repo):
    return ModalSandbox(config).run(AgentTaskSpec(
        repo=repo,
        task="Upgrade requests library to 2.32 and fix any breaking API changes",
    ))

with ThreadPoolExecutor(max_workers=10) as pool:
    results = list(pool.map(run, repos))
```

50 MRs across 50 microservices in the time it takes to make a cup of coffee.

---

## Air-gap architecture

For regulated industries (finance, healthcare, government) where no code, prompt, or diff can touch
the public internet:

```
GitLab on-prem         — code stays inside your network
     ↓
agent-container        — runs on-prem or in private VPC
     ↓
Modal (private VPC)    — or replace Modal with self-hosted container runtime
     ↓
SGLang on-prem GPU     — prompts never leave your network
```

Every agent action produces a GitLab MR with:
- Full diff
- Test results
- Original task prompt in the description
- Timestamp and run ID

This is a complete, immutable audit trail. The agent proposes, humans approve. Nothing merges
without a review.

---

## Compliance checklist

- [ ] Model endpoint is on-prem or in private VPC (`OPENAI_BASE_URL` points internally)
- [ ] GitLab token has minimum required scopes (`api` for MR creation)
- [ ] Modal token scoped to a dedicated org/workspace
- [ ] MR approval rules require at least one human reviewer
- [ ] `SECURITY.md` reviewed and threat model accepted
- [ ] Dependabot alerts reviewed weekly (automated via `.github/dependabot.yml`)
- [ ] pip-audit runs on every CI build
