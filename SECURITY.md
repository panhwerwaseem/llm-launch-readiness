# Security

An agent skill is instructions and code that your coding agent will follow and run. Review a skill before installing it, the same way you would review any dependency.

## What this skill does on your machine

| | |
|---|---|
| Reads | Python, JavaScript and TypeScript source files (up to 1 MB each) under the directory you pass to `readiness_scan.py`, and the names of the other files there. Directories whose name starts with a dot and `node_modules`, `venv`, `site-packages`, `__pycache__`, `dist`, `build`, `vendor` and `coverage` are skipped |
| Writes | Nothing. Both scripts print to standard output and standard error only |
| Executes | Nothing. The scanned code is read as text; it is never imported or run. No subprocess is started |
| Network | None. The bundled scripts import no network modules. `tools/validate.py` checks this in CI |
| Dependencies | Python standard library only |
| Pre-approved tools | None. `SKILL.md` has no `allowed-tools` entry, so your agent's normal permission prompts apply |
| Hooks, MCP servers, background processes | None |

The skill instructs the agent to report and not to edit code during the review. Any fix you ask for afterwards goes through your agent's normal review and permission flow.

## Cautions specific to this skill

- **The scan output quotes your source.** Each finding prints the cited line. Values that look like credentials are cut to their first four characters, but this redaction is pattern-based: a secret in a shape the patterns do not recognise is printed as it appears if its line is cited. Read the output before pasting it into a ticket, a chat or a CI log that others can see.
- **The scan is not a secret scanner.** It looks for a few key shapes in source files only. It does not read `.env` files, configuration files, notebooks or git history. Use a dedicated secret scanner as well.
- **A clean scan is not a security review.** The scan is heuristic. "not found" for a hazard means only that no known pattern matched.
- **The fixtures are unsafe on purpose.** `evals/files/hardcoded-key` contains the fake string `sk-FAKE-not-a-real-key-0000`, which is not a real credential. `evals/files/prompt-injection` contains deliberately vulnerable code. Do not copy code from the fixtures.

## Reporting a vulnerability

Please report security problems privately through the repository's **Security** tab ("Report a vulnerability"). If that option is not available, open an issue that says a private channel is needed, without including exploit details.

## Verifying what you install

- Install from a tagged release and read the diff between tags before updating.
- The whole skill is the `skills/llm-launch-readiness/` folder: one Markdown protocol, two Python scripts, two reference files, and eval fixtures.
