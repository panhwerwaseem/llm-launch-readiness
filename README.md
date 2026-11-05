# llm-launch-readiness

**An agent skill that checks an LLM or agent feature for fallbacks, rate-limit handling, guardrails, cost caps, evals and tracing before release, and backs every verdict with a file and line.**

Left alone, a coding agent asked "is this ready to ship?" tends to skim the main file, list generic best practices, and answer "looks production ready". It does not check each call site, it accepts a function named `fallback` as a fallback, and it treats a few unit tests with a mocked model as evals. This skill replaces that with a scan and a protocol: the scan finds the LLM call sites and cites the lines for each safeguard, and the protocol forbids marking an item as passed without reading those lines, forbids a ready verdict without evals, and forbids fixing anything before the report is delivered.

Works with Claude Code, and with any agent that reads the open [Agent Skills](https://agentskills.io) format (Codex, Cursor, GitHub Copilot, Gemini CLI and others).

## What it looks like

A scan of a fixture app where request fields are placed in the system prompt and a model-chosen argument reaches a shell command. The commands below are run from the `skills/llm-launch-readiness` folder:

```console
$ python3 scripts/readiness_scan.py evals/files/prompt-injection
LLM launch readiness scan (heuristic: pattern matching on source text, not proof)
root: evals/files/prompt-injection   source files scanned: 1   LLM call sites: 1
  call site  src/assistant.ts:40  chat.completions

Safeguards (want: evidence found)
  [evidence found    ] timeout: Request timeout set
        src/assistant.ts:12  const client = new OpenAI({ timeout: 20_000, maxRetries: 3 });
  [evidence found    ] retry: Retry with backoff
        src/assistant.ts:12  const client = new OpenAI({ timeout: 20_000, maxRetries: 3 });
  [evidence found    ] rate_limit: Rate-limit and overload errors handled
        src/assistant.ts:62  if (err instanceof OpenAI.APIError && (err.status === 429 || (err.status ?? 0) >= 500)) {
  [evidence found    ] output_limit: Output token limit set
        src/assistant.ts:40  const completion = await client.chat.completions.create({
  [evidence found    ] fallback: Fallback path
        src/assistant.ts:9  const FALLBACK_MODEL = process.env.ASSISTANT_FALLBACK_MODEL!;
        src/assistant.ts:37  for (const model of [MODEL, FALLBACK_MODEL]) {
        src/assistant.ts:64  console.warn(`model ${model} unavailable (${err.status}); trying the fallback model`);
  [evidence found    ] usage_logging: Token usage and cost recorded
        src/assistant.ts:49  span.setAttribute("llm.prompt_tokens", completion.usage?.prompt_tokens ?? 0);
        src/assistant.ts:50  span.setAttribute("llm.completion_tokens", completion.usage?.completion_tokens ?? 0);
  [evidence found    ] tracing: Tracing around LLM calls
        src/assistant.ts:4  import { trace } from "@opentelemetry/api";
        src/assistant.ts:7  const tracer = trace.getTracer("order-assistant");
        src/assistant.ts:38  const span = tracer.startSpan("llm.answer", { attributes: { "llm.model": model } });
  [evidence found    ] evals: Evals or prompt tests
        evals/  1 file(s)
  [evidence found    ] error_handling: Errors caught around the call
        src/assistant.ts:40  const completion = await client.chat.completions.create({
            ^ inside the try at line 39

Hazards (want: not found)
  [not found         ] hardcoded_keys: No hard-coded API keys
        note: only source files were checked; run a secret scanner on the repository
  [needs human review] untrusted_input: No untrusted input in system prompts or tool calls
        note: trace each cited value to its source; user or retrieved text must not reach a system prompt, and tool arguments chosen by the model must be validated
        src/assistant.ts:35  const system = `You are the order assistant for ${customerName}. Account notes: ${accountNotes}. Answer in two sentences.`;
            ^ system prompt is built from: accountNotes, customerName
        src/assistant.ts:30  return execSync(`./bin/order-status ${orderId}`).toString();
            ^ code or shell execution in a file that calls the LLM
        src/assistant.ts:14  const tools: OpenAI.Chat.Completions.ChatCompletionTool[] = [
            ^ tools are passed to the model

Score: safeguards 9/9 with evidence, 0 need review, 0 not found. Hazards 0 flagged, 1 need review, 1 not found.
Scan result: NEEDS REVIEW of untrusted_input (nothing blocking found)
Heuristic pattern matching. 'evidence found' is not proof that a safeguard works, and 'not found' is not proof that it is missing. Open each cited line before deciding.
```

The scan does not decide that this is unsafe. It cites the three lines, and the protocol makes the agent trace each value before it gives a verdict.

Pointing the scan at code with no LLM call is an error, not a pass:

```console
$ python3 scripts/readiness_scan.py references
readiness_scan.py: no LLM call site recognised in references. Nothing was assessed; this is not a pass. Point the scan at the directory that holds the LLM feature, or add --call-pattern REGEX for an in-house wrapper.
```

## Install

Pick one route. Installing through more than one gives you the skill twice.

**Claude Code (plugin)**

```
/plugin install llm-launch-readiness --marketplace panhwerwaseem/llm-launch-readiness
```

On Claude Code versions before 2.1.275, use two steps:

```
/plugin marketplace add panhwerwaseem/llm-launch-readiness
/plugin install llm-launch-readiness@panhwerwaseem-llm-launch-readiness
```

**Any agent (skills CLI)**

```bash
npx skills add panhwerwaseem/llm-launch-readiness
```

**Manual**

Copy the `skills/llm-launch-readiness` folder into `.claude/skills/` (Claude Code) or `.agents/skills/` (cross-agent) in your project, or into the same folder under your home directory.

To pin a release, add the tag: `/plugin marketplace add panhwerwaseem/llm-launch-readiness#v0.1.0` or `npx skills add panhwerwaseem/llm-launch-readiness@v0.1.0`. Third-party plugins do not update automatically; run `claude plugin update llm-launch-readiness@panhwerwaseem-llm-launch-readiness` to update.

## Use

Describe the launch and the skill loads on its own:

> We turn on the support reply assistant for all customers next week. The code is in services/assistant. Is it ready?

In Claude Code you can also call it directly with `/llm-launch-readiness`.

You can run the scan yourself, for example in CI. It exits 1 when it finds no evals or a hard-coded key, and with `--fail-on gap` also when any safeguard is not found:

```bash
python3 skills/llm-launch-readiness/scripts/readiness_scan.py services/assistant --fail-on gap
```

## What is in the skill

| File | Purpose |
|---|---|
| `SKILL.md` | The protocol: six hard rules and a seven-step workflow from scan to verdict, with the report template |
| `scripts/readiness_scan.py` | Scans Python, JavaScript and TypeScript. Finds LLM call sites, then reports nine safeguards and two hazards with file and line. Text or JSON output |
| `scripts/scan_rules.py` | Every pattern the scan matches, in one file. Run it to print them |
| `references/checklist.md` | For each item: what good looks like, what to confirm in the code, and what does not count. Also the items no repository can show |
| `references/failure-modes.md` | What goes wrong in production LLM features and the safeguard for each |
| `evals/` | Six fixture apps (one clean, five with one planted defect each) with eval cases, plus trigger queries |

### What the scan checks

| Item | Reported as found when |
|---|---|
| `timeout` | A timeout is set in a file that calls the LLM |
| `retry` | A retry limit or backoff is set there. Retries set to 0, or a bare retry loop, give "needs human review" |
| `rate_limit` | 429, overload or rate-limit errors are named there. Generic error handling gives "needs human review" |
| `output_limit` | Every call has an output token limit in its own arguments |
| `fallback` | A fallback, circuit breaker or degraded mode is named there |
| `usage_logging` | Response usage fields are read there |
| `tracing` | A span or a tracing library is used there |
| `evals` | An evals directory or eval file exists. Tests that only reference the LLM code give "needs human review". Nothing at all is blocking |
| `error_handling` | Every call is inside a `try` with a handler in its own function, or has a `.catch` |
| `hardcoded_keys` | Hazard. A string that looks like a credential is flagged, with the value redacted. Blocking |
| `untrusted_input` | Hazard. Cites system prompts built from variables, tools passed to the model, and code or shell execution. Always "needs human review", never a pass or a fail |

### What the scan does not do

- It matches text. It does not parse or run code, follow imports, or know whether a cited line is ever executed. A fallback function that nothing calls is reported as evidence; the fixture `evals/files/dead-fallback` exists to test that the agent catches this by reading the code.
- It cannot see SDK defaults, provider settings, spend limits, quotas, alerting or anything else outside the scanned directory.
- It reads `.py`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.mts` and `.cts` files only. Notebooks, configuration files, `.env` files and other languages are not read.
- Embedding calls are not treated as call sites.
- Its key check is not a secret scanner. Use a dedicated one on the repository and its history.
- A result of `NO GAP FOUND` is not a ready verdict.

## Requirements

- Python 3.9 or later
- No packages to install and no network access

## What it reads, writes and contacts

- **Reads:** source files under the directory you pass (up to 1 MB each) and the names of the other files there. Directories whose name starts with a dot, and dependency and build directories such as `node_modules`, are skipped.
- **Writes:** nothing. Output goes to the terminal.
- **Runs:** nothing. The scanned code is read as text and never imported or executed.
- **Contacts:** nothing. The scripts contain no network code; CI enforces this.
- **Permissions:** the skill pre-approves no tools. Your agent asks before running commands, as usual.

The scan output quotes lines of your source. Values that look like credentials are cut to their first four characters, but review the output before pasting it anywhere public. See [SECURITY.md](SECURITY.md).

## Testing status

- The scripts have unit tests that run in CI on Python 3.9 and 3.12, together with a validator for the skill format and plugin manifests. The tests run the scan on every fixture and check that the console output in this README matches what the script prints.
- The eval cases in `skills/llm-launch-readiness/evals/` are in the [skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator) format. A with-skill versus without-skill score has not been published yet; results are welcome as an issue or pull request.

## Development

```bash
python3 tools/validate.py              # skill format, manifests, no-network check
python3 -m unittest discover -s tests  # script unit tests
```

## License

[MIT](LICENSE)
