# Launch readiness checklist

For each item that `scripts/readiness_scan.py` reports: what good looks like,
what to confirm in the code before marking it verified, and what does not
count. Parameter and field names differ between providers and SDK versions.
Where a name matters, check the documentation for the installed version.

## Contents

- [Timeout](#timeout)
- [Retry with backoff](#retry-with-backoff)
- [Rate-limit and overload handling](#rate-limit-and-overload-handling)
- [Output token limit](#output-token-limit)
- [Fallback path](#fallback-path)
- [Usage and cost recording](#usage-and-cost-recording)
- [Tracing](#tracing)
- [Evals](#evals)
- [Error handling](#error-handling)
- [Hard-coded API keys](#hard-coded-api-keys)
- [Untrusted input](#untrusted-input)
- [Items the repository cannot show](#items-the-repository-cannot-show)

## Timeout

**Good.** Every LLM request has a deadline chosen for the feature: seconds for
an interactive request, longer for a background job. For streaming there is
also a limit on the gap between chunks or on the whole stream.

**Confirm.** The timeout is set on the client that the call site really uses,
or on the call. Its unit is what the SDK expects (some take seconds, some
milliseconds). The value is shorter than the timeout of whatever calls this
code (web server, queue worker, gateway), so this code fails first and can
answer cleanly.

**Does not count.** A timeout on a different HTTP client. An SDK default that
nobody chose: some official SDKs default to a timeout of several minutes. A
timeout of `None` or `null`, which usually means no limit.

## Retry with backoff

**Good.** A small, fixed number of retries for transient failures, with
exponential backoff and jitter, and a total time budget.

**Confirm.** Only transient errors are retried: connection errors, timeouts,
429 and 5xx responses. Client errors such as an invalid request are not. There
is one retry layer, or the layers are known: an SDK that retries inside an
application loop that also retries multiplies the request count. Retried calls
that trigger tools or writes are safe to repeat.

**Does not count.** A loop that retries at once with no delay. Retries set to
zero. A retry around the whole user request that repeats side effects.

**Note.** Some official SDKs retry a small number of times by default. Relying
on that is acceptable when it is a decision: cite the documented default for
the installed version in the report.

## Rate-limit and overload handling

**Good.** A 429 or an overload response is recognised as its own case. The
code waits as the response asks (a `retry-after` header when present), sheds
load or queues work, and tells the user something useful when it gives up.

**Confirm.** What happens after the retries are used up: the error is caught,
logged with enough detail to tell a rate limit from an outage, and turned into
a fallback or a clear message. For batch or agent workloads, concurrency is
bounded so that the feature does not rate-limit itself.

**Does not count.** A bare catch-all that treats every error the same and
retries forever. Catching the error only to log it and re-raise to the user.

## Output token limit

**Good.** Every generation call sets an explicit output limit sized to the
task. Agent loops also cap the number of steps or tool calls per task.

**Confirm.** The limit is in the arguments of each call, or in a shared
parameter object that every call uses. The code checks why generation stopped
and handles a response cut off by the limit, especially before parsing JSON or
running a tool call. Field names vary: for example `finish_reason` with the
value `length` in one API and `stop_reason` with the value `max_tokens` in
another.

**Does not count.** A limit on one call site out of several. A limit constant
that is defined and never passed. A limit on input tokens.

## Fallback path

**Good.** When the primary model or provider is unavailable, the feature does
something planned: a second model or provider, a cached or simpler answer, or
a clear "not available" state that lets the user continue without the feature.

**Confirm.** The fallback is called from the request path, not only defined.
It is triggered by the errors that really occur (timeout, 429, overload), and
it has its own timeout. A fallback model has been run through the evals, since
a prompt tuned for one model can behave differently on another.

**Does not count.** A function or variable named fallback that nothing calls.
A fallback to the same provider and model. A fallback that hides every failure
so that nobody learns the primary path is down.

## Usage and cost recording

**Good.** Each call records model, input tokens, output tokens and latency,
tagged with the feature and, where allowed, the user or tenant. Cost can be
derived from these records, and someone is alerted on unusual spend.

**Confirm.** The usage fields are read from the response and sent to logs or
metrics, not only read. Streaming calls record usage too: with some APIs usage
arrives only in the last event or must be requested. Failed and retried calls
are counted.

**Does not count.** Reading the provider's monthly invoice. Logging the whole
response body in place of the usage numbers.

## Tracing

**Good.** Each LLM call is a span or trace record with model, latency, token
counts, outcome and a request ID that links it to the user request. For
agents, each step and tool call is linked to its parent.

**Confirm.** The span wraps the call sites the scan listed, not only the HTTP
layer. The provider's request ID is captured when the SDK exposes it, so a
failure can be raised with the provider. Prompt and output capture follows the
team's data policy: decide on purpose whether content is recorded, sampled or
left out.

**Does not count.** Web-framework tracing that never mentions the model call.
Application logs with no ID to join them.

## Evals

**Good.** A set of representative and adversarial inputs with expected
properties, run against the real prompt and model, with a pass bar, and re-run
when the prompt, model or retrieval changes.

**Confirm.** The eval calls the production prompt-building code, not a copy of
the prompt. Cases include the failure modes that matter for the feature:
refusals, instructions hidden in user content, long inputs, empty inputs, and
the languages users write in. There is a recorded result for the version being
launched.

**Does not count.** Unit tests that mock the model: they test the plumbing,
not the prompt. A handful of manual tries. An eval directory with no cases.

## Error handling

**Good.** Any failure of the model call ends in a defined result for the user:
a fallback answer or a plain message, with no stack trace, raw provider error
or endless spinner.

**Confirm.** Follow each call site outward to the user. Some caller catches
the SDK's error types, and the user-facing message is written on purpose.
Output that cannot be parsed or fails validation is handled as an error too.
Partial output from a failed stream is not shown as if it were complete.

**Does not count.** A `try` with only `finally`. Catching the error and
returning an empty string that the interface shows as a blank answer. A catch
that swallows the error without logging it.

## Hard-coded API keys

**Good.** Keys come from the environment or a secret store at run time. They
are scoped per environment and can be rotated without a deploy.

**Confirm.** No key literal is in source, tests, notebooks, configuration
files or container images. The scan reads only Python, JavaScript and
TypeScript source: run a dedicated secret scanner over the repository and its
history as well. A key that was ever committed must be rotated, because
removing it from the current files does not remove it from history.

**Does not count.** A key moved to a `.env` file that is committed. A key
"obfuscated" by encoding or splitting it.

## Untrusted input

**Good.** Text from users, documents, web pages, emails and tool results is
passed as data in the user or tool turn. System prompts are fixed or built
only from trusted values. Tool arguments chosen by the model are validated
before use, and tools with side effects need authorisation that the model
cannot grant itself.

**Confirm.** For each value the scan reports as built into a system prompt,
trace it to its source. For each tool, check what a hostile argument could do:
shell commands, SQL, file paths, URLs, payments, email. Check that the model
can reach only data the current user may see. Check that model output is
escaped or validated before it is rendered, executed or stored.

**Does not count.** A sentence in the prompt asking the model to ignore
instructions in user content: it helps, and it is not a control. Filtering a
list of bad words.

## Items the repository cannot show

Ask the owner. List each as "owner to confirm" until answered.

- Provider spend limit and a budget alert that reaches a person
- Rate-limit quota compared with expected peak traffic, with headroom
- A kill switch or feature flag that turns the feature off without a deploy
- A rollback plan for the prompt and the model setting, not only the code
- Alerting on error rate, latency and spend, and who is on call
- What prompts and outputs are stored, where, for how long, and who can read them
- How the team will learn that the model in use is being deprecated
- Whether the launch volume was tested, including behaviour under rate limits
- Terms, privacy and user-facing disclosure that the feature uses a model
