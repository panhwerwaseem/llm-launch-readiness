import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "llm-launch-readiness"
FIXTURES = SKILL / "evals" / "files"
sys.path.insert(0, str(SKILL / "scripts"))

import readiness_scan as scan  # noqa: E402

FOUND, REVIEW, MISSING, FLAGGED = scan.FOUND, scan.REVIEW, scan.MISSING, scan.FLAGGED
FAKE_KEY = "sk-FAKE-not-a-real-key-0000"
SAFEGUARDS = ("timeout", "retry", "rate_limit", "output_limit", "fallback", "usage_logging",
              "tracing", "evals", "error_handling")

# A Python module with every safeguard the scan looks for, used as a base for
# the single-change scenarios below.
GOOD_PY = '''import anthropic
from opentelemetry import trace

tracer = trace.get_tracer(__name__)
client = anthropic.Anthropic(timeout=20.0, max_retries=3)
SYSTEM_PROMPT = "You answer questions about orders."


def answer(question):
    for model in (MODEL, FALLBACK_MODEL):
        try:
            with tracer.start_as_current_span("llm.answer"):
                response = client.messages.create(
                    model=model,
                    max_tokens=300,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": question}],
                )
        except anthropic.RateLimitError:
            continue
        log.info("tokens=%d", response.usage.output_tokens)
        return response.content[0].text
    return "Sorry, try again later."
'''
GOOD_EVALS = {"evals/cases.json": "[]"}


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = scan.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def statuses(report):
    return dict((item["id"], item["status"]) for item in report["items"])


class ScanCase(unittest.TestCase):
    def scan_files(self, files, **kwargs):
        """Write {relative path: text} into a temp directory and scan it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for rel, text in files.items():
            path = Path(tmp.name) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        report, sdk_files = scan.scan(tmp.name, **kwargs)
        self.sdk_files = sdk_files
        return report

    def item(self, report, item_id):
        return [i for i in report["items"] if i["id"] == item_id][0]

    def good(self, **changes):
        files = {"app/bot.py": GOOD_PY}
        files.update(GOOD_EVALS)
        files.update(changes)
        return self.scan_files(files)


class FixtureTests(unittest.TestCase):
    def report(self, name):
        code, out, _ = run_cli([str(FIXTURES / name), "--json"])
        return code, json.loads(out)

    def item(self, report, item_id):
        return [i for i in report["items"] if i["id"] == item_id][0]

    def assert_only_difference(self, report, changed):
        """Every item except the planted one matches the clean fixture."""
        clean = statuses(self.report("ready-app")[1])
        clean.update(changed)
        self.assertEqual(statuses(report), clean)

    def test_ready_app_has_evidence_for_every_safeguard_and_no_hazard(self):
        code, report = self.report("ready-app")
        got = statuses(report)
        self.assertEqual(code, 0)
        for item_id in SAFEGUARDS:
            self.assertEqual(got[item_id], FOUND, item_id)
        self.assertEqual(got["hardcoded_keys"], MISSING)
        self.assertEqual(got["untrusted_input"], MISSING)
        self.assertEqual(report["result"], "no-gap-found")
        self.assertEqual(report["blocking"], [])
        self.assertEqual(report["call_sites"],
                         [{"file": "support_bot/reply.py", "line": 25, "pattern": "messages"}])

    def test_no_evals_is_blocked(self):
        code, report = self.report("no-evals")
        self.assertEqual(code, 1)
        self.assertEqual(report["result"], "blocked")
        self.assertEqual(report["blocking"], ["evals"])
        self.assert_only_difference(report, {"evals": MISSING})

    def test_hardcoded_key_is_flagged_with_its_line_and_never_printed(self):
        code, report = self.report("hardcoded-key")
        item = self.item(report, "hardcoded_keys")
        self.assertEqual(code, 1)
        self.assertEqual(report["blocking"], ["hardcoded_keys"])
        self.assertEqual((item["evidence"][0]["file"], item["evidence"][0]["line"]),
                         ("support_bot/client.py", 11))
        self.assert_only_difference(report, {"hardcoded_keys": FLAGGED})
        _, text, _ = run_cli([str(FIXTURES / "hardcoded-key")])
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn(FAKE_KEY, json.dumps(report))
        self.assertIn("[redacted, 27 chars]", text)

    def test_no_timeout_is_a_gap_and_fails_only_with_fail_on_gap(self):
        code, report = self.report("no-timeout")
        self.assertEqual(code, 0)
        self.assertEqual(report["result"], "gaps")
        self.assertEqual(report["gaps"], ["timeout"])
        self.assert_only_difference(report, {"timeout": MISSING})
        self.assertEqual(run_cli([str(FIXTURES / "no-timeout"), "--fail-on", "gap"])[0], 1)

    def test_prompt_injection_cites_the_system_prompt_the_tool_and_the_shell_call(self):
        code, report = self.report("prompt-injection")
        item = self.item(report, "untrusted_input")
        found = dict((e["line"], e["note"]) for e in item["evidence"])
        self.assertEqual(code, 0)
        self.assertEqual(report["result"], "needs-review")
        self.assertEqual(found[35], "system prompt is built from: accountNotes, customerName")
        self.assertIn("execution", found[30])
        self.assertIn("tools", found[14])
        self.assertEqual(sorted(found), [14, 30, 35])
        self.assert_only_difference(report, {"untrusted_input": REVIEW})

    def test_dead_fallback_is_beyond_the_scan_which_cites_the_unused_function(self):
        # The planted defect is a fallback that nothing calls. A pattern scan
        # cannot see that: it reports evidence and cites the line to read.
        code, report = self.report("dead-fallback")
        lines = [(e["file"], e["line"]) for e in self.item(report, "fallback")["evidence"]]
        self.assertEqual(code, 0)
        self.assertIn(("support_bot/reply.py", 48), lines)
        self.assert_only_difference(report, {})
        handlers = (FIXTURES / "dead-fallback" / "support_bot" / "handlers.py").read_text()
        self.assertNotIn("draft_with_fallback", handlers)

    def test_python_fixtures_are_valid_python(self):
        paths = sorted(FIXTURES.rglob("*.py"))
        self.assertTrue(paths)
        for path in paths:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_fixture_keys_are_the_obvious_fake(self):
        for path in sorted(FIXTURES.rglob("*")):
            if path.is_file():
                for key in re.findall(r"sk-[A-Za-z0-9_\-]{8,}", path.read_text(encoding="utf-8")):
                    self.assertEqual(key, FAKE_KEY, path)


class PatternItemTests(ScanCase):
    def test_base_module_passes_every_safeguard(self):
        report = self.good()
        self.assertEqual([i for i, s in statuses(report).items() if s != FOUND],
                         ["hardcoded_keys", "untrusted_input"])

    def test_timeout_none_needs_review(self):
        report = self.good(**{"app/bot.py": GOOD_PY.replace("timeout=20.0", "timeout=None")})
        item = self.item(report, "timeout")
        self.assertEqual(item["status"], REVIEW)
        self.assertIn("None", item["note"])
        self.assertEqual(item["evidence"][0]["line"], 5)

    def test_retries_set_to_zero_need_review(self):
        report = self.good(**{"app/bot.py": GOOD_PY.replace("max_retries=3", "max_retries=0")})
        item = self.item(report, "retry")
        self.assertEqual(item["status"], REVIEW)
        self.assertIn("set to 0", item["note"])

    def test_retry_loop_without_backoff_needs_review(self):
        source = GOOD_PY.replace(", max_retries=3", "").replace(
            "for model in (MODEL, FALLBACK_MODEL):", "for attempt in (MODEL, FALLBACK_MODEL):")
        item = self.item(self.good(**{"app/bot.py": source}), "retry")
        self.assertEqual(item["status"], REVIEW)
        self.assertIn("no backoff", item["note"])

    def test_missing_retry_is_not_found(self):
        report = self.good(**{"app/bot.py": GOOD_PY.replace(", max_retries=3", "")})
        self.assertEqual(self.item(report, "retry")["status"], MISSING)
        self.assertEqual(report["gaps"], ["retry"])

    def test_generic_error_handling_is_review_for_rate_limits(self):
        source = GOOD_PY.replace("anthropic.RateLimitError", "anthropic.APIError")
        item = self.item(self.good(**{"app/bot.py": source}), "rate_limit")
        self.assertEqual(item["status"], REVIEW)
        self.assertIn("429", item["note"])

    def test_safeguard_seen_only_outside_llm_files_needs_review(self):
        source = GOOD_PY.replace("timeout=20.0, ", "")
        report = self.good(**{"app/bot.py": source,
                              "app/http_util.py": "import os\nDEFAULT = dict(timeout=5)\n"})
        item = self.item(report, "timeout")
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual(item["evidence"][0]["file"], "app/http_util.py")
        self.assertIn("outside", item["note"])

    def test_comments_docstrings_and_prompt_text_are_not_evidence(self):
        source = GOOD_PY.replace("timeout=20.0, ", "").replace(
            "for model in (MODEL, FALLBACK_MODEL):", "for model in (MODEL, OTHER):")
        source += ('\n# TODO: set timeout=30 on the client\n'
                   'HELP = """\nIf the model is down we use a fallback.\ntimeout=30\n"""\n'
                   'def other():\n    """Return the fallback and timeout=5 settings."""\n')
        report = self.good(**{"app/bot.py": source})
        self.assertEqual(self.item(report, "timeout")["status"], MISSING)
        self.assertEqual(self.item(report, "fallback")["status"], MISSING)

    def test_tests_and_evals_are_not_safeguard_evidence(self):
        source = GOOD_PY.replace("timeout=20.0, ", "")
        report = self.good(**{"app/bot.py": source,
                              "tests/test_bot.py": "client = Fake(timeout=1)\n",
                              "evals/run.py": "client = Fake(timeout=1)\n"})
        self.assertEqual(self.item(report, "timeout")["status"], MISSING)


class PerCallTests(ScanCase):
    SECOND_CALL = ('\n\ndef title(text):\n    try:\n        return client.messages.create(\n'
                   '            model=MODEL,\n            messages=[{"role": "user", "content": text}],\n'
                   '        )\n    except anthropic.APIError:\n        return None\n')

    def test_one_call_without_a_limit_needs_review_and_is_cited(self):
        report = self.good(**{"app/bot.py": GOOD_PY + self.SECOND_CALL})
        item = self.item(report, "output_limit")
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual(item["evidence"][0]["line"], 28)
        self.assertIn("no output token limit", item["evidence"][0]["note"])
        self.assertEqual(self.item(report, "error_handling")["status"], FOUND)

    def test_no_limit_anywhere_is_not_found_and_cites_the_call(self):
        report = self.good(**{"app/bot.py": GOOD_PY.replace("max_tokens=300,", "")})
        item = self.item(report, "output_limit")
        self.assertEqual(item["status"], MISSING)
        self.assertEqual((item["evidence"][0]["file"], item["evidence"][0]["line"]),
                         ("app/bot.py", 13))

    def test_limit_passed_through_a_params_dict_needs_review(self):
        source = GOOD_PY.replace("max_tokens=300,", "**PARAMS,")
        source += '\nPARAMS = {"max_tokens": 300}\n'
        item = self.item(self.good(**{"app/bot.py": source}), "output_limit")
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual([e["line"] for e in item["evidence"]], [13, 25])

    def test_limit_in_another_call_on_the_same_line_group_is_not_borrowed(self):
        source = ('import openai\nclient = openai.OpenAI()\n\n\ndef f(q):\n'
                  '    a = client.chat.completions.create(model=M, messages=q)\n'
                  '    b = client.chat.completions.create(model=M, messages=q, max_completion_tokens=9)\n'
                  '    return a, b\n')
        item = self.item(self.scan_files({"app.py": source}), "output_limit")
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual([e["line"] for e in item["evidence"]], [6])

    def test_call_outside_any_try_with_no_try_in_the_file_is_not_found(self):
        source = ('import openai\nclient = openai.OpenAI()\n\n\ndef f(q):\n'
                  '    return client.chat.completions.create(model=M, messages=q)\n')
        item = self.item(self.scan_files({"app.py": source}), "error_handling")
        self.assertEqual(item["status"], MISSING)
        self.assertEqual(item["evidence"][0]["line"], 6)

    def test_try_in_the_caller_needs_review(self):
        source = ('import openai\nclient = openai.OpenAI()\n\n\ndef ask(q):\n'
                  '    return client.chat.completions.create(model=M, messages=q)\n\n\n'
                  'def handler(q):\n    try:\n        return ask(q)\n'
                  '    except openai.APIError:\n        return None\n')
        item = self.item(self.scan_files({"app.py": source}), "error_handling")
        self.assertEqual(item["status"], REVIEW)
        self.assertIn("not inside a try", item["evidence"][0]["note"])

    def test_try_finally_only_is_not_evidence(self):
        source = ('import openai\nclient = openai.OpenAI()\n\n\ndef f(q):\n    try:\n'
                  '        return client.chat.completions.create(model=M, messages=q)\n'
                  '    finally:\n        done()\n')
        item = self.item(self.scan_files({"app.py": source}), "error_handling")
        self.assertEqual(item["status"], REVIEW)

    def test_javascript_catch_chain_counts(self):
        source = ('import OpenAI from "openai";\nconst client = new OpenAI();\n'
                  'export function ask(q) {\n'
                  '  return client.chat.completions.create({\n    model: M,\n    messages: q,\n'
                  '  }).catch((err) => null);\n}\n')
        item = self.item(self.scan_files({"src/ask.js": source}), "error_handling")
        self.assertEqual(item["status"], FOUND)


class EvalsItemTests(ScanCase):
    def base(self, **extra):
        files = {"app/bot.py": GOOD_PY}
        files.update(extra)
        return self.item(self.scan_files(files), "evals")

    def test_nothing_is_not_found(self):
        self.assertEqual(self.base()["status"], MISSING)

    def test_evals_directory_and_file_count(self):
        item = self.base(**{"evals/a.json": "[]", "evals/sub/b.json": "[]"})
        self.assertEqual(item["status"], FOUND)
        self.assertEqual(item["evidence"], [{"file": "evals/", "line": None, "text": "2 file(s)"}])

    def test_eval_file_names_count(self):
        self.assertEqual(self.base(**{"promptfooconfig.yaml": "prompts: []\n"})["status"], FOUND)
        self.assertEqual(self.base(**{"scripts/run_evals.py": "print(1)\n"})["status"], FOUND)

    def test_tests_that_import_the_llm_module_need_review_not_a_pass(self):
        test = ("from unittest import mock\nfrom app.bot import answer\n\n"
                "def test_answer():\n    with mock.patch('app.bot.client'):\n        answer('x')\n")
        item = self.base(**{"tests/test_bot.py": test})
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual((item["evidence"][0]["file"], item["evidence"][0]["line"]),
                         ("tests/test_bot.py", 2))
        self.assertIn("mocks", item["note"])

    def test_unrelated_tests_do_not_count(self):
        item = self.base(**{"tests/test_math.py": "def test_add():\n    assert 1 + 1 == 2\n"})
        self.assertEqual(item["status"], MISSING)

    def test_evals_inside_hidden_or_dependency_directories_do_not_count(self):
        item = self.base(**{".claude/skills/x/evals/evals.json": "{}",
                            "node_modules/pkg/evals/a.json": "[]"})
        self.assertEqual(item["status"], MISSING)


class HazardTests(ScanCase):
    def untrusted(self, files):
        return self.item(self.scan_files(files), "untrusted_input")

    def test_role_system_message_built_from_a_variable(self):
        source = GOOD_PY.replace(
            "system=SYSTEM_PROMPT,", "").replace(
            'messages=[{"role": "user", "content": question}],',
            'messages=[\n                        {"role": "system",\n'
            '                         "content": f"You help {user_name}."},\n'
            '                        {"role": "user", "content": question},\n                    ],')
        item = self.untrusted({"app/bot.py": source})
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual(item["evidence"][0]["note"], "system prompt is built from: user_name")

    def test_interpolated_user_message_after_a_constant_system_prompt_is_not_cited(self):
        source = GOOD_PY.replace(
            'messages=[{"role": "user", "content": question}],',
            'messages=[{"role": "system", "content": SYSTEM_PROMPT},\n'
            '                              {"role": "user", "content": f"Question: {question}"}],')
        self.assertEqual(self.untrusted({"app/bot.py": source})["status"], MISSING)

    def test_system_argument_followed_by_an_interpolated_user_message_on_one_line(self):
        source = ('import anthropic\nclient = anthropic.Anthropic()\n'
                  'r = client.messages.create(model=M, max_tokens=5, system=SYSTEM, '
                  'messages=[{"role": "user", "content": f"{q}"}])\n')
        self.assertEqual(self.untrusted({"app.py": source})["status"], MISSING)

    def test_multi_line_f_string_system_prompt(self):
        source = GOOD_PY.replace(
            'SYSTEM_PROMPT = "You answer questions about orders."',
            'def system_prompt(ticket):\n    system = f"""You answer questions.\n'
            'Customer note: {ticket.note}\n"""\n    return system')
        item = self.untrusted({"app/bot.py": source})
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual(item["evidence"][0]["note"], "system prompt is built from: ticket.note")

    def test_system_prompt_in_a_file_that_does_not_call_the_llm(self):
        prompts = 'def build(notes):\n    system_prompt = "Account notes: " + notes\n    return system_prompt\n'
        item = self.untrusted({"app/bot.py": GOOD_PY, "app/prompts.py": prompts})
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual((item["evidence"][0]["file"], item["evidence"][0]["line"]),
                         ("app/prompts.py", 2))

    def test_prompt_template_placeholder_in_a_system_message(self):
        source = ('from langchain_core.prompts import ChatPromptTemplate\n'
                  'prompt = ChatPromptTemplate.from_messages([\n'
                  '    ("system", "You help staff at {company}. Reply as JSON like {{}}."),\n'
                  '    ("user", "{question}"),\n])\n\n\ndef ask(llm, q):\n'
                  '    return (prompt | llm).invoke({"question": q})\n')
        item = self.untrusted({"chain.py": source})
        self.assertEqual(item["status"], REVIEW)
        self.assertEqual([(e["line"], e["note"]) for e in item["evidence"]],
                         [(3, "system prompt is built from: company")])

    def test_tools_and_execution_in_an_llm_file_need_review(self):
        source = GOOD_PY.replace("max_tokens=300,", "max_tokens=300,\n                    tools=TOOLS,")
        source += "\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n"
        notes = [e["note"] for e in self.untrusted({"app/bot.py": source})["evidence"]]
        self.assertEqual(len(notes), 2)
        self.assertIn("execution", notes[0])
        self.assertIn("tools", notes[1])

    def test_untrusted_input_never_blocks_by_itself(self):
        source = GOOD_PY.replace("max_tokens=300,", "max_tokens=300,\n                    tools=TOOLS,")
        report = self.good(**{"app/bot.py": source})
        self.assertEqual(report["blocking"], [])
        self.assertEqual(report["result"], "needs-review")

    def test_key_in_a_test_file_is_flagged_and_placeholder_is_not(self):
        report = self.good(**{"tests/test_bot.py": 'KEY = "%s"\n' % FAKE_KEY})
        item = self.item(report, "hardcoded_keys")
        self.assertEqual(item["status"], FLAGGED)
        self.assertTrue(item["blocking"])
        self.assertEqual(item["evidence"][0]["file"], "tests/test_bot.py")
        report = self.good(**{"app/cfg.py": 'api_key = "sk-your-api-key-goes-here"\n'})
        self.assertEqual(self.item(report, "hardcoded_keys")["status"], MISSING)


class CallSiteTests(ScanCase):
    def test_generic_call_shape_needs_an_sdk_import_somewhere(self):
        sms = "def send(c):\n    return c.messages.create(to='x', body='y')\n"
        self.assertIsNone(self.scan_files({"sms.py": sms}))
        self.assertEqual(self.sdk_files, [])

    def test_client_imported_from_another_module_is_found(self):
        files = {"llm.py": "import anthropic\nclient = anthropic.Anthropic()\n",
                 "svc.py": "from llm import client\n\ndef f(q):\n    return client.messages.create(q)\n"}
        report = self.scan_files(files)
        self.assertEqual(report["call_sites"], [{"file": "svc.py", "line": 4, "pattern": "messages"}])

    def test_sdk_import_without_a_recognised_call_is_reported(self):
        self.assertIsNone(self.scan_files({"llm.py": "import anthropic\nc = anthropic.Anthropic()\n"}))
        self.assertEqual(self.sdk_files, ["llm.py"])

    def test_custom_call_pattern(self):
        files = {"svc.py": "def f(q):\n    return gateway.complete(q, max_tokens=5)\n"}
        self.assertIsNone(self.scan_files(files))
        report = self.scan_files(files, call_patterns=[re.compile(r"gateway\.complete\(")])
        self.assertEqual(report["call_sites"], [{"file": "svc.py", "line": 2, "pattern": "custom"}])
        self.assertEqual(self.item(report, "output_limit")["status"], FOUND)

    def test_raw_http_endpoint_is_a_call_site(self):
        source = 'URL = "https://api.openai.com/v1/chat/completions"\n'
        report = self.scan_files({"client.js": source})
        self.assertEqual(report["call_sites"][0]["pattern"], "http endpoint")
        for item_id in ("output_limit", "error_handling"):
            item = self.item(report, item_id)
            self.assertEqual(item["status"], MISSING)
            self.assertIn("built by hand", item["evidence"][0]["note"])

    def test_calls_in_tests_evals_comments_and_skipped_directories_do_not_count(self):
        call = "r = client.chat.completions.create(model=M)\n"
        files = {"tests/test_x.py": call, "evals/run.py": call, "node_modules/p/i.js": call,
                 ".venv/lib/x.py": call, "legacy/old.py": call, "a.py": "# " + call}
        self.assertIsNone(self.scan_files(files, exclude=["legacy"]))
        self.assertIsNotNone(self.scan_files(files))

    def test_unreadable_extensions_are_ignored_and_large_files_are_listed(self):
        files = {"app/bot.py": GOOD_PY, "notes.md": "client.chat.completions.create(",
                 "app/big.py": "x = 1\n" * 200000}
        report = self.scan_files(files)
        self.assertEqual(report["skipped_files"], ["app/big.py"])
        self.assertEqual(report["files_scanned"], 1)


class CliTests(unittest.TestCase):
    def test_text_output_is_labelled_heuristic_and_never_says_ready_without_qualification(self):
        code, out, _ = run_cli([str(FIXTURES / "ready-app")])
        self.assertEqual(code, 0)
        self.assertIn("heuristic", out.splitlines()[0])
        self.assertIn("NO GAP FOUND. This is not a ready verdict.", out)
        self.assertIn("not proof", out.splitlines()[-1])

    def test_json_report_is_labelled_heuristic(self):
        _, out, _ = run_cli([str(FIXTURES / "ready-app"), "--json"])
        report = json.loads(out)
        self.assertIs(report["heuristic"], True)
        self.assertIn("not proof", report["disclaimer"])
        self.assertEqual(report["score"]["safeguards"]["total"], 9)
        self.assertEqual(report["score"]["hazards"]["total"], 2)

    def test_every_evidence_entry_names_a_file(self):
        for name in sorted(p.name for p in FIXTURES.iterdir() if p.is_dir()):
            report = json.loads(run_cli([str(FIXTURES / name), "--json"])[1])
            for item in report["items"]:
                if item["status"] in (FOUND, REVIEW, FLAGGED):
                    self.assertTrue(item["evidence"], (name, item["id"]))
                for entry in item["evidence"]:
                    self.assertTrue(entry["file"])
                    self.assertTrue(entry["line"] is None or entry["line"] >= 1)

    def test_output_is_deterministic(self):
        first = run_cli([str(FIXTURES / "prompt-injection"), "--json"])
        self.assertEqual(first, run_cli([str(FIXTURES / "prompt-injection"), "--json"]))

    def test_missing_directory_is_a_usage_error(self):
        code, _, err = run_cli([str(FIXTURES / "nope")])
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_no_call_site_is_exit_2_and_says_it_is_not_a_pass(self):
        code, out, err = run_cli([str(SKILL / "references")])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("this is not a pass", err)
        self.assertIn("--call-pattern", err)

    def test_invalid_call_pattern_and_evidence_limit_are_usage_errors(self):
        self.assertEqual(run_cli([str(FIXTURES / "ready-app"), "--call-pattern", "("])[0], 2)
        self.assertEqual(run_cli([str(FIXTURES / "ready-app"), "--max-evidence", "0"])[0], 2)

    def test_max_evidence_limits_text_but_not_json(self):
        _, out, _ = run_cli([str(FIXTURES / "ready-app"), "--max-evidence", "1"])
        self.assertIn("(+4 more; use --json for all)", out)

    def test_help_documents_examples_and_exit_codes(self):
        code, out, _ = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("Examples:", out)
        self.assertIn("Exit codes:", out)

    def test_script_runs_directly_from_another_directory(self):
        proc = subprocess.run(
            [sys.executable, "-B", str(SKILL / "scripts" / "readiness_scan.py"),
             str(FIXTURES / "no-evals")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT.parent))
        self.assertEqual(proc.returncode, 1, proc.stderr.decode())
        self.assertIn("Scan result: BLOCKED by evals", proc.stdout.decode())


class ReadmeTests(unittest.TestCase):
    def test_console_blocks_in_readme_match_real_output(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```console\n\$ python3 scripts/readiness_scan\.py ([^\n]*)\n(.*?)```",
                            readme, re.S)
        self.assertGreaterEqual(len(blocks), 2)
        here = os.getcwd()
        os.chdir(str(SKILL))
        try:
            for argv, shown in blocks:
                _, out, err = run_cli(argv.split())
                self.assertEqual(shown, out + err, argv)
        finally:
            os.chdir(here)


class ValidateRepoTests(unittest.TestCase):
    def test_repository_passes_its_own_validator(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import validate
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = validate.main(["validate.py", str(ROOT)])
        self.assertEqual(code, 0, buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
