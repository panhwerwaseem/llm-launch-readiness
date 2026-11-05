# Failure modes of LLM features in production

What goes wrong after launch, how it shows up, and the safeguard that limits
it. Use this to rank the gaps from the review and to explain to the user what
a gap costs. Item names in brackets are the IDs printed by
`scripts/readiness_scan.py`.

## Contents

- [Availability](#availability)
- [Cost](#cost)
- [Quality](#quality)
- [Security and privacy](#security-and-privacy)
- [Operations](#operations)

## Availability

**The provider is slow or down.** Requests hang. Web workers or queue slots
fill with waiting calls, and parts of the product that do not use the model
slow down with it. Safeguards: a request deadline shorter than the caller's
own [timeout]; a planned degraded mode [fallback]; a kill switch.

**Rate limits at launch.** Traffic on launch day is above anything tested. The
provider answers 429, and users see errors in bursts. Safeguards: quota checked
against expected peak; bounded concurrency; waiting as the response asks
[rate_limit]; a queue or a clear "busy" message instead of a failure
[error_handling].

**Retry storm.** Every layer retries: the SDK, the application, the job
runner, the user pressing the button again. One failed call becomes many, and
the extra load keeps the provider's limit tripped. Safeguards: one retry
layer with backoff and jitter [retry]; a total time budget; no retry on
errors that will not change.

**Fallback that does not work.** The fallback exists in the code and was never
exercised: it is not called, uses a prompt the second model handles badly, or
has no timeout of its own. It fails on the day it is needed. Safeguards: call
it from the request path [fallback]; include it in the evals [evals]; test it
by forcing the primary path to fail.

## Cost

**Runaway output.** A request with no output limit, or an agent loop with no
step limit, produces far more tokens than intended. The bill shows it later.
Safeguards: an output limit on every call [output_limit]; a cap on steps and
tool calls per task; a provider spend limit.

**Cost nobody can explain.** Spend rises and no record says which feature,
tenant or prompt caused it. Safeguards: usage recorded per call with feature
and tenant tags [usage_logging]; an alert on spend, not only the invoice.

**Abuse.** A public endpoint is used as a free model proxy, or one account
sends very large inputs. Safeguards: authentication and per-user limits in
front of the feature; input size limits; the output limit.

## Quality

**Silent regression.** A prompt edit, a model change or a retrieval change
makes answers worse. Nothing fails, so nobody notices until users complain.
Safeguards: evals run on every such change with a pass bar [evals]; traces
that let a bad answer be reproduced [tracing].

**Truncated or malformed output.** The answer is cut off at the output limit,
or the JSON does not parse, and the code downstream crashes or stores half a
result. Safeguards: check why generation stopped; validate structured output
before use; treat a failed validation as an error with a defined result
[error_handling].

**Input larger than the context window.** A long document or a long
conversation makes the request fail, or pushes the instructions out of
effect. Safeguards: measure and bound input size before the call; a defined
behaviour for oversize input; an eval case with long input [evals].

**Model retired or changed.** The model the feature depends on is deprecated,
or an alias starts pointing to a different version. Behaviour changes with no
deploy. Safeguards: the model name in configuration, not scattered in code;
an owner who tracks deprecation notices; evals re-run before switching.

## Security and privacy

**Prompt injection.** Text the model reads (a user message, a web page, a
file, an email, a tool result) contains instructions, and the model follows
them. The damage is whatever the model can reach: other users' data, tools
with side effects, the system prompt. Safeguards: untrusted text kept out of
system prompts [untrusted_input]; least privilege for tools and data;
validation of tool arguments; confirmation before destructive actions.

**Unsafe use of output.** Model output is rendered as HTML, run as code, used
as a shell command, a file path or a query. Safeguards: treat model output as
untrusted input to the rest of the system [untrusted_input]: escape, validate
and restrict it.

**Leaked key.** A key in source, a notebook, a client-side bundle or a log is
found and used by someone else. Safeguards: keys only in the environment or a
secret store [hardcoded_keys]; a secret scanner in CI; rotation after any
exposure; keys never sent to the browser.

**Sensitive data in logs and traces.** Prompts and outputs that contain
personal or confidential data are stored in logging and tracing systems with
wide access and long retention. Safeguards: decide what content is recorded
[tracing]; redact or sample; set retention and access on purpose.

## Operations

**No way to turn it off.** The feature misbehaves in production, and the only
way to stop it is a deploy. Safeguards: a kill switch or feature flag; a
rollback plan that covers the prompt and model settings.

**Nobody is told.** Errors, latency or spend drift for days. Safeguards:
alerts on error rate, latency and spend that reach a person; an owner on call.

**Cannot reproduce a bad answer.** A user reports a wrong or harmful answer,
and no record links their request to the prompt, model and output.
Safeguards: a request ID carried through traces [tracing]; the provider's
request ID captured when the SDK exposes it.
