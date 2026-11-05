---
name: llm-launch-readiness
description: Checks an LLM or agent feature for launch readiness by scanning the code for timeouts, retries with backoff, rate-limit handling, output token limits, fallbacks, usage logging, tracing, evals, hard-coded API keys, prompt-injection paths and error handling, then verifying each item in the code and giving a ready or not-ready verdict with file and line evidence. Use when the user asks whether an LLM, chatbot, RAG or agent feature is ready to ship, launch, release or go to production, asks for a production-readiness, pre-launch or go-live review or checklist for code that calls a model API, or says things like is this safe to launch, what are we missing before we ship the assistant, or harden this LLM integration before release.
license: MIT
compatibility: Requires Python 3.9 or later to run the bundled scripts. No network access and no extra packages needed. The scan reads Python, JavaScript and TypeScript source.
metadata:
  author: panhwerwaseem
  version: "0.1.0"
---

# LLM launch readiness

The review is done when every checklist item has one of three outcomes that
you can defend: verified in code with a file and line you have read, reported
as a gap or blocker with where you looked, or handed to a named owner because
it cannot be seen in the repository. The verdict follows from those outcomes,
not from the scan's score.

Paths in this file are relative to this skill's directory.

## Hard rules

1. **The scan is a lead, not a finding.** `scripts/readiness_scan.py` matches
   patterns in source text. "not found" is a prompt to look, not proof of
   absence: search for the safeguard yourself before reporting a gap.
   "evidence found" is not proof it works: open the cited line and confirm it
   is on the path of the LLM call and does what the item requires.
2. **No pass without a pointer.** Mark an item as verified only with a file
   and line that you have read. An item you could not confirm is a gap or
   "owner to confirm", never a pass.
3. **No evals, no ready verdict.** If nothing runs the real prompts and checks
   the output, the verdict is NOT READY. Unit tests that mock the model are not
   evals.
4. **Credentials and untrusted input block.** A credential in source, or user
   or retrieved text that reaches a system prompt or the arguments of a tool
   with side effects without validation, is a blocker. Never print a found
   credential in full. Say that it must be rotated, not only removed.
5. **Report, do not fix.** Do not edit code, configuration or dependencies
   during the review, even if the request says "check and fix". Deliver the
   report first. Apply a fix only after the user has seen the report and
   chosen it.
6. **Say what was not checked.** State that the scan is heuristic, list the
   call sites, languages and directories the review did not cover, and never
   present the scan's score as a measure of safety. Only the user can accept a
   risk; do not accept one for them.

## Workflow

### 1. Fix the scope

Identify the feature and the directory that holds it. Note whether the model
can call tools, whether its output is shown to end users, and where its input
comes from. Ask only if the directory cannot be determined.

### 2. Run the scan

```bash
python3 scripts/readiness_scan.py <feature directory>
```

It lists the LLM call sites, then nine safeguards and two hazards, each with a
status and cited lines. Add `--json` for every evidence line. Exit code 0
means nothing blocking was found, 1 means a blocking finding (no evals, or a
credential in source), and 2 means the scan could not run.

If it exits 2 with "no LLM call site recognised", the calls go through a
wrapper or a language the scan does not read. Find the wrapper and pass it:

```bash
python3 scripts/readiness_scan.py <feature directory> --call-pattern 'llm_gateway\.complete\('
```

`python3 scripts/scan_rules.py` prints every pattern the scan matches.

### 3. Confirm the call sites

Read the call-site list. Search the repository yourself for calls it missed:
wrappers, other languages, prompts built in other modules, background jobs.
A safeguard on one call site is not a safeguard on the others.

### 4. Verify each item in the code

For every item, act on the scan status:

| Scan status | What to do |
|---|---|
| evidence found | Open each cited line. Confirm it applies to every call site and is reachable (called, not dead code, not test-only) |
| not found | Search under other names, in wrapper modules, configuration and infrastructure code. Report a gap only after that, and say where you looked |
| needs human review | Read the note. Trace the cited value or call until you can say pass, gap or blocker |
| flagged | Open the line. Confirm it is a real credential or a fixture. Do not print the value |

`references/checklist.md` says, for each item, what good looks like, what to
confirm in the code, and what does not count. Use it for the pass decision.

### 5. Check what the repository cannot show

Ask the user about each of these, or list them as "owner to confirm": provider
spend limit and budget alert, rate-limit quota against expected traffic, a
kill switch or feature flag, the rollback plan, alerting and on-call, what
prompts and outputs are stored and for how long, and how model deprecation is
tracked. `references/checklist.md` has the full list.

### 6. Decide the verdict

- **NOT READY**: any blocker. Blockers are: no evals; a credential in source;
  untrusted input reaching a system prompt or a side-effecting tool without
  validation; an LLM failure that reaches the user as a crash or a raw error.
- **READY WITH ACCEPTED RISKS**: no blockers, and every remaining gap has been
  accepted by the user by name.
- **READY**: no blockers, no gaps, every item verified with a pointer.

Open "owner to confirm" items do not change a code-level verdict, but list
them next to it. `references/failure-modes.md` explains what each gap costs in
production; use it to rank gaps and to explain them.

### 7. Report, then stop

Give the report below. Offer fixes in order of risk. After any fix the user
asks for, re-run the scan and re-verify the changed items.

## Report

```
Launch readiness: <feature>        Verdict: NOT READY | READY WITH ACCEPTED RISKS | READY
Scope: <directory>; <N> LLM call sites (<file:line>, ...)
Method: heuristic scan (readiness_scan.py) plus reading every cited line

Blockers
- <item>: <what is wrong> (<file:line>)

Gaps
- <item>: <what is missing>; looked in <where>

Verified
- <item>: <file:line> <what the code does>

Owner to confirm (not visible in the repository)
- <item>: <question for the owner>

Not checked: <call sites, languages, directories or behaviour not covered>
```

## Edge cases

- **The scan says NO GAP FOUND.** That is not a verdict. Step 4 still applies:
  the scan cannot tell a fallback that is called from one that is dead code.
- **SDK defaults.** Some SDKs retry and time out by default. A default counts
  as verified only if you cite the documented default for the installed
  version and it suits the feature. A default timeout of minutes does not suit
  an interactive request.
- **Language the scan does not read** (Go, Java, Ruby and others). Do the
  checklist by hand and say that the scan was not used.
- **Frameworks.** With LangChain, the AI SDK and similar, limits are often set
  on a model object in another file. Expect "needs human review" and follow
  the object to where it is built.
- **Agents and tool loops.** Check for a cap on steps or tool calls per task
  and for confirmation before destructive tools. The scan does not check these.
- **Streaming.** A stream can fail after output has started. Check what the
  user sees when it does, and that usage is still recorded.
- **Monorepo.** Scan the feature directory and the shared client package
  separately. Use `--exclude` for vendored or legacy directories.
- **Fixture keys.** A flagged value in a test or fixture may be a fake.
  Confirm with the user instead of assuming.
