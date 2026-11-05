#!/usr/bin/env python3
"""Heuristic launch-readiness scan for code that calls an LLM API.

Walks a repository (Python, JavaScript, TypeScript), finds LLM call sites by
SDK import and call pattern, and reports each readiness item as one of:

  evidence found      a matching pattern on the path of the LLM calls
  needs human review  a partial or ambiguous signal; the note says why
  not found           no matching pattern
  flagged             hazard checks only: a pattern that should not be there

Every result is a heuristic from pattern matching on source text. "evidence
found" does not show the safeguard works, and "not found" does not show it is
missing. Each result cites file and line so a person can check it. The
patterns live in scan_rules.py, next to this file.

Examples:
  readiness_scan.py .
  readiness_scan.py services/assistant --json
  readiness_scan.py . --call-pattern 'llm_gateway\\.complete\\('
  readiness_scan.py . --fail-on gap --exclude legacy

Exit codes: 0 = scan ran and found nothing blocking, 1 = blocking finding (no
evals or prompt tests, or a hard-coded key; with --fail-on gap, also any
safeguard not found), 2 = usage or input error, including "no LLM call site
recognised". Standard library only. Reads files only. Makes no network calls.
Writes only to stdout and stderr.
"""
import argparse
import json
import os
import re
import sys

from scan_rules import (
    CALL_PATTERNS, CONTENT_RE, EVAL_DIRS, EVAL_FILE_RE, LANG, MAX_BYTES, MAX_LINE, OUTPUT_RE,
    PATTERN_CHECKS, SDK_IMPORT, SINK_RE, SKIP_DIRS, SYSTEM_RE, TOOLS_RE, TRY_RE, enclosing_try,
    extent, file_kind, interpolations, is_comment, key_spans, prose_lines, redact)

FOUND, REVIEW, MISSING, FLAGGED = "evidence found", "needs human review", "not found", "flagged"


class Source(object):
    """One source file: relative path, language, kind (product, test, eval) and lines."""

    def __init__(self, rel, lang, lines):
        self.rel, self.lang, self.lines = rel, lang, lines
        self.kind = file_kind(rel)
        # Comment lines and lines inside multi-line strings are never evidence.
        self.skip = prose_lines(lines, lang) | set(
            idx for idx, line in enumerate(lines) if is_comment(line, lang))
        self.has_sdk = any(SDK_IMPORT[lang].search(line) for idx, line in enumerate(lines)
                           if idx not in self.skip)
        self.calls = []  # (line index, pattern label, column of the opening bracket or -1)


def evidence(src, idx, note=None):
    item = {"file": src.rel, "line": idx + 1, "text": redact(src.lines[idx].strip())[:160]}
    if note:
        item["note"] = note
    return item


def collect(root, skip_dirs):
    """Return (sources, every relative path, skipped paths), all sorted."""
    sources, paths, skipped = [], [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in skip_dirs)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            paths.append(rel)
            lang = LANG.get(os.path.splitext(name)[1].lower())
            if not lang or name.endswith((".min.js", ".d.ts")):
                continue
            try:
                if os.path.getsize(full) > MAX_BYTES:
                    skipped.append(rel)
                    continue
                with open(full, "r", encoding="utf-8", errors="replace") as handle:
                    lines = [line[:MAX_LINE] for line in handle.read().splitlines()]
                sources.append(Source(rel, lang, lines))
            except OSError:
                skipped.append(rel)
    return sorted(sources, key=lambda s: s.rel), sorted(paths), sorted(skipped)


def find_calls(src, repo_has_sdk, patterns):
    calls = []
    for idx, line in enumerate(src.lines):
        if idx in src.skip:
            continue
        for label, scope, regex in patterns:
            if (scope == "repo" and not repo_has_sdk) or (scope == "file" and not src.has_sdk):
                continue
            match = regex.search(line)
            if match:
                col = line.find("(", match.end() - 1)
                calls.append((idx, label, col))
                break
    return calls


def check_call(src, idx, col):
    """Return (output limit in the call's arguments, try line or True if handled)."""
    body = "\n".join(src.lines[idx:idx + 80])[col + 1:] if col >= 0 else ""  # -1: no bracket
    args = extent(body, False)
    handled = enclosing_try(src.lines, src.lang, idx, src.skip)
    if not handled and src.lang == "js":
        handled = ".catch(" in "\n".join(body[len(args):].split("\n")[:2])
    return bool(OUTPUT_RE.search(args)), handled


def system_prompt_hits(src):
    """Lines where a system prompt is assembled from variables."""
    hits = []
    for idx, line in enumerate(src.lines):
        match = None if idx in src.skip else SYSTEM_RE.search(line)
        if not match:
            continue
        text = "\n".join(src.lines[idx:idx + 60])[match.end():]
        if match.group("role"):  # {"role": "system", "content": ...}
            content = CONTENT_RE.search(text)
            between = text[:content.start()] if content else "role"
            if "role" in between or between.count("\n") > 3:
                continue
            text = text[content.end():]
        value = extent(text.lstrip(), True)
        names = interpolations(value, src.lang)
        if match.group("template"):  # placeholders are filled in when the template is used
            names = sorted(set(names + re.findall(r"(?<!\{)\{(\w+)\}(?!\})", value)))
        if names:
            hits.append(evidence(src, idx, "system prompt is built from: " + ", ".join(names[:5])))
    return hits


def grep(sources, regex, skip=None, note=None, first_per_file=False):
    hits = []
    for src in sources:
        for idx, line in enumerate(src.lines):
            if idx in src.skip or not regex.search(line) or (skip and skip.search(line)):
                continue
            hits.append(evidence(src, idx, note))
            if first_per_file:
                break
    return hits


def make(item_id, title, kind, status, found=(), note=""):
    return {"id": item_id, "title": title, "kind": kind, "status": status, "blocking": False,
            "note": note, "evidence": list(found)}


def pattern_item(check, llm, others):
    args = (check["id"], check["title"], "safeguard")
    found = grep(llm, check["found"], check["off"])
    if found:
        return make(*args, status=FOUND, found=found)
    outside = "matched only outside the files that call the LLM; check that it covers those calls"
    for regex, pool, why in ((check["off"], llm, check.get("off_note")),
                             (check["weak"], llm, check.get("weak_note")),
                             (check["found"], others, outside)):
        hits = grep(pool, regex) if regex else []
        if hits:
            return make(*args, status=REVIEW, found=hits, note=why)
    return make(*args, status=MISSING, note="look for " + check["look"])


def per_call_items(llm, product):
    """Output limit and error handling, judged at each call site."""
    limit_ok, limit_bad, caught_ok, caught_bad = [], [], [], []
    for src in llm:
        for idx, label, col in src.calls:
            if label == "http endpoint":  # a URL, not a call: there are no arguments to read
                by_hand = evidence(src, idx, "the request is built by hand; find where this URL "
                                             "is used and check that request")
                limit_bad.append(by_hand)
                caught_bad.append(by_hand)
                continue
            has_limit, handled = check_call(src, idx, col)
            (limit_ok if has_limit else limit_bad).append(evidence(
                src, idx, None if has_limit else "no output token limit in this call's arguments"))
            (caught_ok if handled else caught_bad).append(evidence(
                src, idx, "inside the try at line %d" % handled if handled and handled is not True
                else None if handled else "not inside a try with a handler in its own function"))
    seen = set((e["file"], e["line"]) for e in limit_ok + limit_bad)
    elsewhere = [e for e in grep(product, OUTPUT_RE) if (e["file"], e["line"]) not in seen]
    any_try = any(TRY_RE[s.lang].search(line) for s in llm for line in s.lines)
    results = []
    for item_id, title, good, bad, backup, look, why in (
            ("output_limit", "Output token limit set", limit_ok, limit_bad, elsewhere,
             "an output token limit on every call (max_tokens, max_completion_tokens, "
             "max_output_tokens, maxOutputTokens)",
             "a limit exists, but not in the arguments of every call; check each cited call"),
            ("error_handling", "Errors caught around the call", caught_ok, caught_bad, [],
             "a try/except or try/catch around each call that returns a clear message to the user",
             "some calls are not inside a try in their own function; check that a caller "
             "catches the error and what the user sees")):
        if good and not bad:
            results.append(make(item_id, title, "safeguard", FOUND, good))
        elif good or backup or (item_id == "error_handling" and any_try):
            results.append(make(item_id, title, "safeguard", REVIEW, bad + backup, why))
        else:
            results.append(make(item_id, title, "safeguard", MISSING, bad, "look for " + look))
    return results


def evals_item(sources, paths, llm):
    args = ("evals", "Evals or prompt tests", "safeguard")
    counts = {}
    for rel in paths:
        parts = rel.split("/")
        hit = [i for i, part in enumerate(parts[:-1]) if part.lower() in EVAL_DIRS]
        if hit:
            key = "/".join(parts[:hit[0] + 1]) + "/"
        elif EVAL_FILE_RE.search(parts[-1]):
            key = rel
        else:
            continue
        counts[key] = counts.get(key, 0) + 1
    if counts:
        found = [{"file": key, "line": None,
                  "text": "%d file(s)" % counts[key] if key.endswith("/") else "eval file"}
                 for key in sorted(counts)]
        return make(*args, status=FOUND, found=found)
    stems = sorted(set(os.path.splitext(os.path.basename(s.rel))[0] for s in llm)
                   - {"__init__", "index"})
    stem_re = re.compile(r"\b(?:import|from|require)\b.*\b(?:%s)\b"
                         % "|".join(re.escape(s) for s in stems)) if stems else None
    hits = []
    for src in (s for s in sources if s.kind == "test"):
        for idx, line in enumerate(src.lines):
            if (SDK_IMPORT[src.lang].search(line) or (stem_re and stem_re.search(line))
                    or any(regex.search(line) for _l, scope, regex in CALL_PATTERNS
                           if scope == "any")):
                hits.append(evidence(src, idx))
                break
    if hits:
        return make(*args, status=REVIEW, found=hits, note=(
            "tests reference the LLM code, but no eval suite was found; check that they run "
            "the real prompts and assert on the output, not only on mocks"))
    return make(*args, status=MISSING, note=(
        "look for an evals directory, eval files, or tests that run the prompts"))


def hazard_items(sources, llm, product):
    keys = [evidence(src, idx) for src in sources
            for idx, line in enumerate(src.lines) if key_spans(line)]
    key_item = make("hardcoded_keys", "No hard-coded API keys", "hazard",
                    FLAGGED if keys else MISSING, keys,
                    "a string that looks like a credential is in the source; move it to the "
                    "environment or a secret store and rotate it" if keys else
                    "only source files were checked; run a secret scanner on the repository")
    risky = [hit for src in product for hit in system_prompt_hits(src)]
    risky += grep(llm, SINK_RE, note="code or shell execution in a file that calls the LLM")
    risky += grep(llm, TOOLS_RE, note="tools are passed to the model", first_per_file=True)
    input_item = make("untrusted_input", "No untrusted input in system prompts or tool calls",
                      "hazard", REVIEW if risky else MISSING, risky,
                      "trace each cited value to its source; user or retrieved text must not "
                      "reach a system prompt, and tool arguments chosen by the model must be "
                      "validated" if risky else
                      "no interpolated system prompt, tool use or execution sink was seen")
    return [key_item, input_item]


def scan(root, call_patterns=(), exclude=()):
    """Return (report, []), or (None, files that import an LLM SDK) when no call
    site was recognised and so nothing could be assessed."""
    sources, paths, skipped = collect(root, SKIP_DIRS | set(exclude))
    product = [s for s in sources if s.kind == "product"]
    repo_has_sdk = any(s.has_sdk for s in product)
    patterns = CALL_PATTERNS + [("custom", "any", regex) for regex in call_patterns]
    for src in product:
        src.calls = find_calls(src, repo_has_sdk, patterns)
    llm = [s for s in product if s.has_sdk or s.calls]
    sites = [{"file": s.rel, "line": idx + 1, "pattern": label}
             for s in llm for idx, label, _col in s.calls]
    if not sites:
        return None, [s.rel for s in llm]
    others = [s for s in product if not (s.has_sdk or s.calls)]
    by_id = dict((c["id"], pattern_item(c, llm, others)) for c in PATTERN_CHECKS)
    by_id.update((item["id"], item) for item in per_call_items(llm, product))
    by_id["evals"] = evals_item(sources, paths, llm)
    order = ("timeout", "retry", "rate_limit", "output_limit", "fallback", "usage_logging",
             "tracing", "evals", "error_handling")
    items = [by_id[i] for i in order] + hazard_items(sources, llm, product)
    for item in items:
        item["blocking"] = ((item["id"] == "evals" and item["status"] == MISSING)
                            or item["status"] == FLAGGED)
    score = {}
    for kind in ("safeguard", "hazard"):
        group = [i["status"] for i in items if i["kind"] == kind]
        score[kind + "s"] = dict((s, group.count(s)) for s in (FOUND, REVIEW, MISSING, FLAGGED))
        score[kind + "s"]["total"] = len(group)
    blocking = [i["id"] for i in items if i["blocking"]]
    gaps = [i["id"] for i in items
            if i["kind"] == "safeguard" and i["status"] == MISSING and not i["blocking"]]
    review = [i["id"] for i in items if i["status"] == REVIEW]
    return {
        "heuristic": True, "root": root, "files_scanned": len(sources), "skipped_files": skipped,
        "call_sites": sites, "items": items, "score": score, "blocking": blocking, "gaps": gaps,
        "needs_review": review,
        "result": ("blocked" if blocking else "gaps" if gaps else "needs-review" if review
                   else "no-gap-found"),
        "disclaimer": "Heuristic pattern matching. 'evidence found' is not proof that a safeguard "
                      "works, and 'not found' is not proof that it is missing. Open each cited "
                      "line before deciding.",
    }, []


def render(report, limit):
    out = ["LLM launch readiness scan (heuristic: pattern matching on source text, not proof)",
           "root: %s   source files scanned: %d   LLM call sites: %d" % (
               report["root"], report["files_scanned"], len(report["call_sites"]))]
    for site in report["call_sites"][:10]:
        out.append("  call site  %s:%d  %s" % (site["file"], site["line"], site["pattern"]))
    if len(report["call_sites"]) > 10:
        out.append("  (+%d more call sites; use --json for all)" % (len(report["call_sites"]) - 10))
    for kind, heading in (("safeguard", "Safeguards (want: evidence found)"),
                          ("hazard", "Hazards (want: not found)")):
        out += ["", heading]
        for item in (i for i in report["items"] if i["kind"] == kind):
            out.append("  [%-18s] %s: %s%s" % (item["status"], item["id"], item["title"],
                                             "  (BLOCKING)" if item["blocking"] else ""))
            if item["note"]:
                out.append("        note: " + item["note"])
            for found in item["evidence"][:limit]:
                where = found["file"] if found["line"] is None else "%s:%d" % (
                    found["file"], found["line"])
                out.append("        %s  %s" % (where, found["text"]))
                if found.get("note"):
                    out.append("            ^ " + found["note"])
            if len(item["evidence"]) > limit:
                out.append("        (+%d more; use --json for all)" % (len(item["evidence"]) - limit))
    safe, haz = report["score"]["safeguards"], report["score"]["hazards"]
    out += ["", "Score: safeguards %d/%d with evidence, %d need review, %d not found. "
                "Hazards %d flagged, %d need review, %d not found." % (
                    safe[FOUND], safe["total"], safe[REVIEW], safe[MISSING],
                    haz[FLAGGED], haz[REVIEW], haz[MISSING])]
    if report["skipped_files"]:
        out.append("Skipped (unreadable or over %d bytes): %s" % (
            MAX_BYTES, ", ".join(report["skipped_files"])))
    if report["result"] == "blocked":
        out.append("Scan result: BLOCKED by %s" % ", ".join(report["blocking"]))
    elif report["result"] == "gaps":
        out.append("Scan result: GAPS in %s (nothing blocking found)" % ", ".join(report["gaps"]))
    elif report["result"] == "needs-review":
        out.append("Scan result: NEEDS REVIEW of %s (nothing blocking found)" % ", ".join(
            report["needs_review"]))
    else:
        out.append("Scan result: NO GAP FOUND. This is not a ready verdict.")
    out.append(report["disclaimer"])
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", default=".", help="directory to scan (default: .)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--call-pattern", action="append", default=[], metavar="REGEX",
                        help="extra regex that marks an LLM call, for in-house wrappers; repeatable")
    parser.add_argument("--exclude", action="append", default=[], metavar="DIR",
                        help="directory name to skip; repeatable")
    parser.add_argument("--fail-on", choices=("blocker", "gap"), default="blocker",
                        help="exit 1 on blocking findings only (default), or also on any "
                             "safeguard that was not found")
    parser.add_argument("--max-evidence", type=int, default=3, metavar="N",
                        help="evidence lines printed per item in text output (default 3)")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.path):
        parser.error("not a directory: %s" % args.path)
    if args.max_evidence < 1:
        parser.error("--max-evidence must be at least 1")
    try:
        extra = [re.compile(pattern) for pattern in args.call_pattern]
    except re.error as exc:
        parser.error("--call-pattern is not a valid regular expression: %s" % exc)
    report, sdk_files = scan(args.path, extra, args.exclude)
    if report is None:
        hint = ("An LLM SDK is imported in %s, but no call was recognised. "
                % ", ".join(sdk_files[:3])) if sdk_files else ""
        print("readiness_scan.py: no LLM call site recognised in %s. %sNothing was assessed; "
              "this is not a pass. Point the scan at the directory that holds the LLM feature, "
              "or add --call-pattern REGEX for an in-house wrapper." % (args.path, hint),
              file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render(report, args.max_evidence))
    if report["blocking"] or (args.fail_on == "gap" and report["gaps"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
