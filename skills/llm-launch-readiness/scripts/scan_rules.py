#!/usr/bin/env python3
"""The patterns and text helpers that readiness_scan.py uses.

Everything the scan can match is in this file, so it can be audited in one
place. Run it to print the rules:

Examples:
  scan_rules.py            # list every pattern
  scan_rules.py --json     # the same, machine-readable

Exit codes: 0 = rules printed, 2 = usage error. This tool reports no findings,
so it never exits 1. Standard library only. Reads no files. Makes no network
calls. Writes only to stdout.
"""
import argparse
import json
import re
import sys

LANG = {".py": "py", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
        ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js"}
SKIP_DIRS = {"node_modules", "venv", "site-packages", "__pycache__", "dist", "build",
             "vendor", "coverage"}  # directories whose name starts with "." are skipped too
EVAL_DIRS = {"evals", "eval", "evaluation", "evaluations"}
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "e2e"}
EVAL_FILE_RE = re.compile(r"(?i)(?:^|[_.\-])evals?(?:[_.\-]|$)|^promptfooconfig\.")
TEST_FILE_RE = re.compile(
    r"(?i)^(?:test_.*\.py|.*_test\.py|conftest\.py|.*\.(?:test|spec)\.[cm]?[jt]sx?)$")
MAX_BYTES = 1000000  # larger files are skipped and listed
MAX_LINE = 2000      # longer lines are cut; they are generated or minified code

SDK_IMPORT = {
    "py": re.compile(
        r"^\s*(?:import|from)\s+(?:openai|anthropic|mistralai|cohere|litellm|groq|together|"
        r"ollama|vertexai|langchain\w*|llama_index|google\.generativeai|google\.genai)\b"
        r"|^\s*from\s+google\s+import\s+genai\b|[\"']bedrock-runtime[\"']"),
    "js": re.compile(
        r"(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*\(?\s*)[\"']"
        r"(?:openai|ai|langchain|llamaindex|ollama|cohere-ai|groq-sdk|together-ai|"
        r"@anthropic-ai/[\w.-]+|@google/generative-ai|@google/genai|@ai-sdk/[\w.-]+|"
        r"@langchain/[\w.-]+|@mistralai/mistralai|@aws-sdk/client-bedrock-runtime)"
        r"(?:/[\w./-]+)?[\"']"),
}
# (label, scope, regex). Scope says how specific the pattern is: "any" counts in
# every product file, "repo" only when some product file imports an LLM SDK,
# "file" only when the same file imports one.
CALL_PATTERNS = [(label, scope, re.compile(regex)) for label, scope, regex in [
    ("chat.completions", "any", r"\.chat\.completions\.(?:create|parse|stream)\s*\("),
    ("responses", "repo", r"\.responses\.(?:create|parse|stream)\s*\("),
    ("messages", "repo", r"\.messages\.(?:create|stream)\s*\("),
    ("completions", "repo", r"(?<!chat)\.completions\.create\s*\("),
    ("generate_content", "any", r"\.generate_?[cC]ontent(?:_stream|Stream)?\s*\("),
    ("AI SDK", "any", r"\b(?:generateText|streamText|generateObject|streamObject)\s*\("),
    ("litellm", "any", r"\blitellm\.a?completion\s*\("),
    ("chat.complete", "repo", r"\.chat\.(?:complete|stream)(?:_async)?\s*\("),
    ("bedrock", "repo", r"\.(?:converse|invoke_model)(?:_stream|_with_response_stream)?\s*\("
                        r"|\bnew\s+(?:Converse|InvokeModel)\w*Command\s*\("),
    ("invoke", "file", r"\.a?invoke\s*\("),
    ("chat", "file", r"\b(?:ollama|co|cohere)\.(?:chat|generate)\s*\(|(?<![.\w])a?completion\s*\("),
    ("http endpoint", "any", r"api\.openai\.com|api\.anthropic\.com|api\.mistral\.ai|api\.groq\.com"
                             r"|generativelanguage\.googleapis\.com|openrouter\.ai/api"),
]]

# Safeguards checked by pattern. "found" is evidence. "weak" is a partial signal
# and "off" is the safeguard switched off on that line; both give "needs human
# review". A match counts as found only in a file that calls the LLM.
PATTERN_CHECKS = [
    {"id": "timeout", "title": "Request timeout set",
     "found": r"(?i)timeout\w*[\"']?\s*(?:=(?!=)|:)\s*[\w.({\[-]|AbortController"
              r"|AbortSignal\.timeout|asyncio\.(?:wait_for|timeout)\b",
     "off": r"(?i)timeout[\"']?\s*[=:]\s*(?:None|null|undefined)\b",
     "off_note": "the timeout is None, null or undefined on this line",
     "look": "a timeout on the client or on each call (timeout=, AbortSignal, asyncio.wait_for)"},
    {"id": "retry", "title": "Retry with backoff",
     "found": r"(?i)max_?retries|num_?retries|backoff|tenacity|exponential|jitter|p-retry"
              r"|async-retry|with_retry",
     "weak": r"(?i)retr(?:y|ies)|\battempts?\b",
     "weak_note": "retry-like code, but no backoff or retry-limit signal",
     "off": r"(?i)(?:max|num)_?retries[\"']?\s*[=:]\s*0\b",
     "off_note": "retries are set to 0 on this line",
     "look": "a retry limit with backoff (max_retries, maxRetries, tenacity, a backoff loop)"},
    {"id": "rate_limit", "title": "Rate-limit and overload errors handled",
     "found": r"(?i)rate.?limit|\b(?:429|529)\b|retry.?after|overloaded|ThrottlingException"
              r"|ResourceExhausted|TooManyRequests|ServiceUnavailable",
     "weak": r"APIStatusError|APIError|InternalServerError|\bstatus(?:_code|Code)?\b",
     "weak_note": "generic API error handling; check what happens on a 429 or an overload",
     "look": "handling of 429 and overload responses (RateLimitError, status 429, Retry-After)"},
    {"id": "fallback", "title": "Fallback path",
     "found": r"(?i)fallback|circuit.?breaker|degrad|(?:backup|secondary|alternate).?model",
     "look": "a fallback model, cached or canned answer, or a degraded mode"},
    {"id": "usage_logging", "title": "Token usage and cost recorded",
     "found": r"(?<![A-Za-z_])(?:prompt|completion|input|output|total)_tokens\b"
              r"|(?<![A-Za-z])(?:prompt|completion|input|output|total)Tokens\b|\busage\b"
              r"|usage_metadata|usageMetadata|token_count|tokenCount|\bcost(?:_usd|Usd|_cents)?\b",
     "look": "code that reads the response usage fields and logs or meters them"},
    {"id": "tracing", "title": "Tracing around LLM calls",
     "found": r"(?i)opentelemetry|langsmith|langfuse|traceloop|openinference|helicone|braintrust"
              r"|mlflow|ddtrace|dd-trace|start_as_current_span|startActiveSpan|startSpan"
              r"|\btracer\b|@traceable|@observe|\btrace_?id\b",
     "look": "a span or trace around the call (OpenTelemetry or an LLM tracing tool)"},
]
for _check in PATTERN_CHECKS:
    for _key in ("found", "weak", "off"):
        _check[_key] = re.compile(_check[_key]) if _check.get(_key) else None

# Checked at each call site.
OUTPUT_RE = re.compile(
    r"(?i)(?<![a-z_])max_?(?:completion_?|output_?|new_?)?tokens(?:_to_sample)?\b|\bnum_predict\b")
TRY_RE = {"py": re.compile(r"^\s*try\s*:"), "js": re.compile(r"\btry\s*\{|\.catch\s*\(")}
BOUNDARY_RE = {
    "py": re.compile(r"(?:async\s+def|def|class)\b"),
    "js": re.compile(r"\bfunction\b|=>\s*\{\s*$|\)\s*(?::[^={]+)?\{\s*$"),
}
JS_CONTROL_RE = re.compile(r"\b(?:if|for|while|switch|else|catch|finally|do|try)\b")

# Hazards.
KEY_PREFIX_RE = re.compile(r"(?:[\"'`]|Bearer\s+)((?:sk-|AIza)[A-Za-z0-9_\-]{16,})")
KEY_ASSIGN_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret[_-]?key|client[_-]?secret|(?:access|auth|bearer)[_-]?token"
    r"|password)[\"']?\s*[:=]\s*[\"'`]([A-Za-z0-9_\-]{20,})[\"'`]")
PLACEHOLDER_RE = re.compile(r"(?i)your|example|placeholder|changeme|replace|x{4,}")
SYSTEM_RE = re.compile(
    r"(?i)(?P<role>[\"']?role[\"']?\s*:\s*[\"'](?:system|developer)[\"'])"
    r"|\bsystem(?:_?(?:prompt|message|instructions?))?[\"']?\s*(?:=(?!=)|:)"
    r"|\binstructions[\"']?\s*(?:=(?!=)|:)|SystemMessage\s*\("
    r"|(?P<template>\(\s*[\"']system[\"']\s*,)")  # ("system", "... {name} ...") prompt templates
CONTENT_RE = re.compile(r"[\"']?content[\"']?\s*:")
TOOLS_RE = re.compile(r"\btools[\"']?\s*(?:=(?!=)|:)|\bbind_tools\b|\btool_choice\b|\btoolChoice\b"
                      r"|@tool\b|\bfunctions[\"']?\s*(?:=(?!=)|:)")
SINK_RE = re.compile(r"(?<![.\w])(?:eval|exec|execSync)\s*\(|\bos\.system\s*\(|\bsubprocess\.\w+\s*\("
                     r"|\bshell\s*=\s*True|\bnew\s+Function\s*\(")
STRING_RE = re.compile(
    r"(?s)(?:\b[rRbBfFuU]{1,2})?(?:\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\\n])*\""
    r"|'(?:\\.|[^'\\\n])*'|`.*?`)")
FSTRING_RE = re.compile(
    r"(?s)(?<![A-Za-z0-9_])[rR]?[fF][rR]?(?:\"\"\"(.*?)\"\"\"|'''(.*?)'''"
    r"|\"((?:\\.|[^\"\\\n])*)\"|'((?:\\.|[^'\\\n])*)')")
FORMAT_RE = re.compile(r"\.format\s*\(([^)]*)\)|[\"']\s*%\s*\(?([^)\n,]+)")
CONCAT_RE = re.compile(
    r"([A-Za-z_][\w.]*(?:\([^()]*\)|\[[^\[\]]*\])?)(?=\s*\+(?!=))|(?<=\+)\s*([A-Za-z_][\w.]*)")


def file_kind(rel):
    """Classify a relative path as "eval", "test" or "product"."""
    parts = rel.split("/")
    if any(p.lower() in EVAL_DIRS for p in parts[:-1]) or EVAL_FILE_RE.search(parts[-1]):
        return "eval"
    if any(p.lower() in TEST_DIRS for p in parts[:-1]) or TEST_FILE_RE.match(parts[-1]):
        return "test"
    return "product"


def is_comment(line, lang):
    """True for a whole-line comment. Trailing comments are not recognised."""
    text = line.lstrip()
    return text.startswith("#") if lang == "py" else text.startswith(("//", "*", "/*"))


def prose_lines(lines, lang):
    """Indexes of lines that begin inside a multi-line string: docstrings and
    prompt text. Their words are prose, so they are not counted as evidence."""
    multi = ('"""', "'''") if lang == "py" else ("`",)
    comment = "#" if lang == "py" else "//"
    inside, marked = None, set()
    for idx, line in enumerate(lines):
        if inside or (lang == "py" and line.lstrip().startswith(multi)):
            marked.add(idx)  # the second test catches one-line docstrings
        if not inside and not any(mark in line for mark in multi):
            continue
        quote, i = None, 0  # quote is an open one-line string
        while i < len(line):
            if line[i] == "\\" and (inside or quote):
                i += 2
                continue
            if inside:
                if line.startswith(inside, i):
                    i += len(inside) - 1
                    inside = None
            elif quote:
                quote = None if line[i] == quote else quote
            elif line.startswith(multi, i):
                inside = [mark for mark in multi if line.startswith(mark, i)][0]
                i += len(inside) - 1
            elif line[i] in "\"'":
                quote = line[i]
            elif line.startswith(comment, i):
                break
            i += 1
    return marked


def indent_of(line):
    return len(line) - len(line.lstrip(" \t"))


def key_spans(line):
    """Spans of string values on this line that look like credentials."""
    spans = set()
    for regex, generic in ((KEY_PREFIX_RE, False), (KEY_ASSIGN_RE, True)):
        for match in regex.finditer(line):
            value = match.group(1)
            if PLACEHOLDER_RE.search(value):
                continue
            if generic and (re.match(r"[A-Z0-9_]+$", value) or not re.search(r"\d", value)
                            or not re.search(r"[A-Za-z]", value)):
                continue  # an environment variable name or a plain word, not a key
            spans.add(match.span(1))
    return sorted(spans)


def redact(line):
    """Replace each credential-looking value with its first four characters."""
    for start, end in reversed(key_spans(line)):
        line = "%s...[redacted, %d chars]%s" % (line[:start + 4], end - start, line[end:])
    return line


def extent(text, value):
    """Return the leading part of text that is one expression. With value=True it
    ends at a top-level comma, semicolon or line end (an assigned value). With
    value=False it ends only at the unmatched closing bracket (call arguments)."""
    depth, quote, i = 0, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if text.startswith(quote, i):
                i += len(quote)
                quote = None
                continue
            if ch == "\n" and quote in ("'", '"'):
                quote = None  # an unterminated one-line string, or an apostrophe in a comment
        elif text.startswith(('"""', "'''"), i):
            quote = text[i:i + 3]
            i += 3
            continue
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif value and depth == 0 and ch in ",;\n":
            joined = (text[:i].rstrip().endswith(("+", "\\", "%", "=", ":"))
                      or text[i + 1:].lstrip().startswith(("+", "%", ".")))
            if ch != "\n" or not joined:
                break
        i += 1
    return text[:i]


def enclosing_try(lines, lang, idx, skip=()):
    """1-based line of a try that has a handler and encloses line idx inside the
    same function, else None. Uses indentation, so it needs formatted code.
    skip holds the indexes of comment and prose lines, which are ignored."""
    current = indent_of(lines[idx])
    for j in range(idx - 1, -1, -1):
        text = lines[j].strip()
        if not text or j in skip or indent_of(lines[j]) >= current:
            continue
        current = indent_of(lines[j])
        is_try = (re.match(r"try\s*:", text) if lang == "py"
                  else re.search(r"\btry\s*\{\s*$", text))
        if is_try:
            for k in range(j + 1, len(lines)):
                after = lines[k].strip()
                if after and k not in skip and indent_of(lines[k]) <= current:
                    if re.match(r"except\b" if lang == "py" else r"\}?\s*catch\b", after):
                        return j + 1
                    break  # try/finally only: nothing is caught here
        elif lang == "py" and BOUNDARY_RE["py"].match(text):
            return None
        elif lang == "js" and not JS_CONTROL_RE.search(text) and BOUNDARY_RE["js"].search(text):
            return None
    return None


def interpolations(expr, lang):
    """Names and expressions spliced into the string expression expr."""
    found = []
    if lang == "py":
        for parts in FSTRING_RE.findall(expr):
            body = "".join(parts).replace("{{", "").replace("}}", "")
            found += re.findall(r"\{([^{}]+)\}", body)
        found += [a or b for a, b in FORMAT_RE.findall(expr)]
    else:
        found += re.findall(r"\$\{([^{}]+)\}", expr)
    found += [a or b for a, b in CONCAT_RE.findall(STRING_RE.sub('""', expr))]
    return sorted(set(f.strip() for f in found if f.strip()))


def rules():
    """Every pattern, as plain data."""
    single = (("output_limit", OUTPUT_RE), ("hardcoded_keys: known prefix", KEY_PREFIX_RE),
              ("hardcoded_keys: assignment", KEY_ASSIGN_RE),
              ("hardcoded_keys: placeholder, ignored", PLACEHOLDER_RE),
              ("untrusted_input: system prompt", SYSTEM_RE),
              ("untrusted_input: tools", TOOLS_RE), ("untrusted_input: execution", SINK_RE),
              ("error_handling: python", TRY_RE["py"]), ("error_handling: javascript", TRY_RE["js"]),
              ("evals: file name", EVAL_FILE_RE), ("tests: file name", TEST_FILE_RE))
    return {
        "sdk_imports": dict((lang, regex.pattern) for lang, regex in sorted(SDK_IMPORT.items())),
        "call_patterns": [{"label": label, "scope": scope, "regex": regex.pattern}
                          for label, scope, regex in CALL_PATTERNS],
        "safeguard_patterns": [
            {"id": check["id"], "kind": kind, "regex": check[kind].pattern}
            for check in PATTERN_CHECKS for kind in ("found", "weak", "off") if check[kind]],
        "other_patterns": [{"id": name, "regex": regex.pattern} for name, regex in single],
        "eval_dirs": sorted(EVAL_DIRS), "test_dirs": sorted(TEST_DIRS),
        "skipped_dirs": sorted(SKIP_DIRS), "extensions": sorted(LANG),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print the rules as JSON")
    args = parser.parse_args(argv)
    data = rules()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print("SDK imports")
    for lang, regex in sorted(data["sdk_imports"].items()):
        print("  %-4s %s" % (lang, regex))
    print("Call patterns (scope: any = every product file, repo = only when an LLM SDK is "
          "imported somewhere, file = only when this file imports one)")
    for rule in data["call_patterns"]:
        print("  %-17s %-5s %s" % (rule["label"], rule["scope"], rule["regex"]))
    print("Safeguard patterns (found = evidence, weak and off = needs human review)")
    for rule in data["safeguard_patterns"]:
        print("  %-14s %-6s %s" % (rule["id"], rule["kind"], rule["regex"]))
    print("Other patterns")
    for rule in data["other_patterns"]:
        print("  %s\n      %s" % (rule["id"], rule["regex"]))
    for key in ("eval_dirs", "test_dirs", "skipped_dirs", "extensions"):
        print("%s: %s" % (key, ", ".join(data[key])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
