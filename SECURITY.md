# Security

The harness runs model-proposed shell commands and file edits. Treat it as code execution. It's built for one operator on a local machine, bound to loopback, with an explicit repository allowlist.

The full model, with trust boundaries and what is and isn't enforced, is in [docs/security.md](docs/security.md).

## Reporting a vulnerability

Open a private security advisory on this repository, or email p@pdj.dev. Please don't file a public issue with a working exploit or a leaked secret.

## Operator checklist

- Keep `workspace.roots` small and intentional.
- Don't bind the server to a public interface.
- Don't commit `.harness/`, `.env`, or API keys.
- Assume a live model will try to read files you didn't intend. The allowlist and worktree are the boundary, not the prompt.
