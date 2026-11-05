import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "llm-launch-readiness" / "scripts"))

import scan_rules as rules  # noqa: E402

FAKE_KEY = "sk-FAKE-not-a-real-key-0000"


def check(check_id):
    return [c for c in rules.PATTERN_CHECKS if c["id"] == check_id][0]


def call_label(line, scopes=("any", "repo", "file")):
    for label, scope, regex in rules.CALL_PATTERNS:
        if scope in scopes and regex.search(line):
            return label
    return None


class FileKindTests(unittest.TestCase):
    def test_eval_directories_and_eval_file_names(self):
        for rel in ("evals/cases.json", "app/eval/run.py", "run_evals.py", "summary.eval.ts",
                    "promptfooconfig.yaml"):
            self.assertEqual(rules.file_kind(rel), "eval", rel)

    def test_test_directories_and_test_file_names(self):
        for rel in ("tests/test_reply.py", "src/__tests__/a.ts", "src/reply.test.ts",
                    "reply_test.py", "conftest.py", "src/reply.spec.tsx"):
            self.assertEqual(rules.file_kind(rel), "test", rel)

    def test_words_that_only_contain_eval_or_test_are_product(self):
        for rel in ("app/retrieval.py", "app/medieval.py", "src/latest.ts", "app/contest.py"):
            self.assertEqual(rules.file_kind(rel), "product", rel)


class ProseLineTests(unittest.TestCase):
    def test_lines_inside_a_python_docstring_are_prose(self):
        lines = ['PROMPT = """You are helpful.', "Use the fallback if needed.", '"""', "x = 1"]
        self.assertEqual(rules.prose_lines(lines, "py"), {1, 2})

    def test_one_line_docstring_is_prose(self):
        self.assertEqual(rules.prose_lines(['    """Try the fallback model."""', "x = 1"], "py"), {0})

    def test_triple_quote_inside_a_string_or_comment_does_not_open_a_block(self):
        lines = ["marks = ('\"\"\"', \"'''\")", "x = 1  # \"\"\"", "y = 2"]
        self.assertEqual(rules.prose_lines(lines, "py"), set())

    def test_javascript_template_literal_with_an_escaped_backtick(self):
        lines = ["const p = `You are helpful.", "Say \\`hi\\` first.", "Bye.`;", "const x = 1;"]
        self.assertEqual(rules.prose_lines(lines, "js"), {1, 2})

    def test_backtick_inside_a_javascript_string_or_comment_is_ignored(self):
        lines = ["const tick = '`';", "const x = 1; // `", "const y = 2;"]
        self.assertEqual(rules.prose_lines(lines, "js"), set())


class KeyTests(unittest.TestCase):
    def test_key_with_known_prefix_is_found_and_redacted(self):
        line = 'client = Anthropic(api_key="%s", timeout=20)' % FAKE_KEY
        self.assertEqual(len(rules.key_spans(line)), 1)
        redacted = rules.redact(line)
        self.assertNotIn(FAKE_KEY, redacted)
        self.assertIn("sk-F...[redacted, 27 chars]", redacted)
        self.assertIn("timeout=20", redacted)

    def test_bearer_header_and_second_prefix(self):
        self.assertTrue(rules.key_spans('headers = {"Authorization": "Bearer %s"}' % FAKE_KEY))
        self.assertTrue(rules.key_spans('GOOGLE = "AIzaFAKE-not-a-real-key-000000"'))

    def test_generic_assignment_with_letters_and_digits(self):
        self.assertTrue(rules.key_spans('API_KEY = "FAKEfake0000notarealkey1111"'))
        self.assertTrue(rules.key_spans('const cfg = { apiKey: "FAKEfake0000notarealkey1111" };'))

    def test_environment_lookups_names_and_placeholders_are_not_keys(self):
        for line in ('api_key = os.environ["ANTHROPIC_API_KEY"]',
                     'API_KEY_NAME = "ANTHROPIC_API_KEY_PRODUCTION"',
                     'api_key = "your-api-key-goes-here-1234"',
                     'api_key = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"',
                     'api_key = "short1"',
                     'const apiKey = process.env.OPENAI_API_KEY;',
                     'tokenizer = "sentence-transformers-all-MiniLM-L6-v2"',
                     'task_name = "risk-assessment-pipeline-v2"'):
            self.assertEqual(rules.key_spans(line), [], line)


class ExtentTests(unittest.TestCase):
    def test_value_stops_at_a_top_level_comma(self):
        text = 'f"Hi {name}", messages=[{"role": "user", "content": f"{question}"}])'
        self.assertEqual(rules.extent(text, True), 'f"Hi {name}"')

    def test_value_keeps_brackets_and_commas_inside_them(self):
        self.assertEqual(rules.extent('build(a, b) + "x", other', True), 'build(a, b) + "x"')

    def test_value_spans_a_multi_line_string(self):
        text = 'f"""Line one\nNote: {note}\n"""\nnext_statement = 1'
        self.assertEqual(rules.extent(text, True), 'f"""Line one\nNote: {note}\n"""')

    def test_value_follows_a_trailing_plus_onto_the_next_line(self):
        self.assertEqual(rules.extent('"You are " +\n  name;\nrest()', True), '"You are " +\n  name')

    def test_call_arguments_stop_at_the_closing_bracket(self):
        text = '\n    model=m,\n    max_tokens=5,  # don\'t raise\n    messages=[a, (b)],\n)\nprint(1)'
        self.assertEqual(rules.extent(text, False), text[:text.index(")\nprint")])

    def test_bracket_inside_a_string_is_not_counted(self):
        self.assertEqual(rules.extent('"a ) b", x)', False), '"a ) b", x')


class EnclosingTryTests(unittest.TestCase):
    def find(self, source, needle, lang):
        lines = source.splitlines()
        idx = [i for i, line in enumerate(lines) if needle in line][0]
        skip = rules.prose_lines(lines, lang) | set(
            i for i, line in enumerate(lines) if rules.is_comment(line, lang))
        return rules.enclosing_try(lines, lang, idx, skip)

    def test_python_call_inside_try_except(self):
        source = ("def f():\n    try:\n        with span():\n            r = CALL(\n"
                  "                a=1)\n    except Exception:\n        return None\n")
        self.assertEqual(self.find(source, "CALL", "py"), 2)

    def test_python_try_finally_catches_nothing(self):
        source = "def f():\n    try:\n        r = CALL()\n    finally:\n        close()\n"
        self.assertIsNone(self.find(source, "CALL", "py"))

    def test_python_try_outside_the_function_does_not_count(self):
        source = ("try:\n    def f():\n        return CALL()\nexcept Exception:\n    pass\n")
        self.assertIsNone(self.find(source, "CALL", "py"))

    def test_python_call_inside_the_except_block_is_not_protected(self):
        source = ("def f():\n    try:\n        a()\n    except Exception:\n        CALL()\n")
        self.assertIsNone(self.find(source, "CALL", "py"))

    def test_python_prose_at_low_indent_does_not_break_the_walk(self):
        source = ('def f():\n    try:\n        p = """\nunindented prompt text\n"""\n'
                  "        r = CALL()\n    except Exception:\n        pass\n")
        self.assertEqual(self.find(source, "CALL", "py"), 2)

    def test_javascript_call_inside_try_catch(self):
        source = ("async function f() {\n  for (const m of models) {\n    try {\n"
                  "      const r = await CALL({\n        a: 1,\n      });\n"
                  "    } catch (err) {\n      log(err);\n    }\n  }\n}\n")
        self.assertEqual(self.find(source, "CALL", "js"), 3)

    def test_javascript_try_finally_catches_nothing(self):
        source = ("async function f() {\n  try {\n    return await CALL();\n"
                  "  } finally {\n    done();\n  }\n}\n")
        self.assertIsNone(self.find(source, "CALL", "js"))

    def test_javascript_function_boundary_stops_the_search(self):
        source = ("try {\n  app.post(\"/x\", async (req, res) => {\n    await CALL();\n  });\n"
                  "} catch (err) {\n  log(err);\n}\n")
        self.assertIsNone(self.find(source, "CALL", "js"))


class InterpolationTests(unittest.TestCase):
    def test_python_f_string_names(self):
        self.assertEqual(rules.interpolations('f"Hi {name}, notes: {ticket.notes}"', "py"),
                         ["name", "ticket.notes"])

    def test_python_escaped_braces_and_plain_strings_are_not_interpolation(self):
        self.assertEqual(rules.interpolations('f"Reply as JSON: {{\\"ok\\": true}}"', "py"), [])
        self.assertEqual(rules.interpolations('"Use {curly} braces freely"', "py"), [])
        self.assertEqual(rules.interpolations('("You are a bot. "\n "Be brief.")', "py"), [])

    def test_python_format_percent_and_concatenation(self):
        self.assertEqual(rules.interpolations('"Hi {}".format(user_name)', "py"), ["user_name"])
        self.assertEqual(rules.interpolations('"Hi %s" % user_name', "py"), ["user_name"])
        self.assertEqual(rules.interpolations('"Notes: " + notes', "py"), ["notes"])
        self.assertEqual(rules.interpolations('BASE + extra', "py"), ["BASE", "extra"])

    def test_javascript_template_and_concatenation(self):
        self.assertEqual(rules.interpolations("`Hi ${name}, ${req.body.notes}`", "js"),
                         ["name", "req.body.notes"])
        self.assertEqual(rules.interpolations('"Notes: " + notes', "js"), ["notes"])
        self.assertEqual(rules.interpolations('"No variables here"', "js"), [])


class PatternTests(unittest.TestCase):
    def test_python_sdk_imports(self):
        regex = rules.SDK_IMPORT["py"]
        for line in ("import openai", "from anthropic import Anthropic", "from google import genai",
                     "import google.generativeai as genai", "from langchain_openai import ChatOpenAI",
                     'client = boto3.client("bedrock-runtime")', "    import litellm"):
            self.assertTrue(regex.search(line), line)
        for line in ("import openai_helpers", "import os", "from mylib.anthropic import x",
                     "# import openai is not needed"):
            self.assertFalse(regex.search(line) and not rules.is_comment(line, "py"), line)

    def test_javascript_sdk_imports(self):
        regex = rules.SDK_IMPORT["js"]
        for line in ('import OpenAI from "openai";', "const A = require('@anthropic-ai/sdk');",
                     'import { generateText } from "ai";', 'import { openai } from "@ai-sdk/openai";',
                     'import { ChatOpenAI } from "@langchain/openai";',
                     'const { GoogleGenAI } = await import("@google/genai");'):
            self.assertTrue(regex.search(line), line)
        for line in ('import x from "openai-helpers";', 'import fs from "node:fs";',
                     'import { ai } from "./ai";', 'import mail from "email";'):
            self.assertFalse(regex.search(line), line)

    def test_call_patterns(self):
        cases = {
            "r = client.chat.completions.create(": "chat.completions",
            "r = client.messages.create(": "messages",
            "with client.messages.stream(": "messages",
            "r = client.responses.create(": "responses",
            "r = model.generate_content(prompt)": "generate_content",
            "const r = await model.generateContent(prompt);": "generate_content",
            "const { text } = await generateText({": "AI SDK",
            "r = litellm.completion(model=m, messages=msgs)": "litellm",
            "r = client.chat.complete(model=m, messages=msgs)": "chat.complete",
            "r = bedrock.converse(modelId=m, messages=msgs)": "bedrock",
            "const cmd = new ConverseCommand({ modelId });": "bedrock",
            "r = chain.invoke({\"q\": q})": "invoke",
            'URL = "https://api.openai.com/v1/chat/completions"': "http endpoint",
        }
        for line, label in cases.items():
            self.assertEqual(call_label(line), label, line)
        for line in ("r = client.embeddings.create(", "items.create(", "x = complete(y)",
                     "result = generate(text)"):
            self.assertIsNone(call_label(line), line)

    def test_only_specific_patterns_count_without_an_sdk_import(self):
        self.assertEqual(call_label("client.chat.completions.create(", ("any",)), "chat.completions")
        self.assertIsNone(call_label("twilio.messages.create(", ("any",)))
        self.assertIsNone(call_label("fn.invoke(payload)", ("any", "repo")))

    def test_output_limit_names(self):
        for text in ("max_tokens=512", "max_completion_tokens: 400", "max_output_tokens=1",
                     "maxOutputTokens: 256", "maxTokens: 256", "MAX_OUTPUT_TOKENS", "num_predict",
                     "max_new_tokens=64"):
            self.assertTrue(rules.OUTPUT_RE.search(text), text)
        for text in ("max_input_tokens=5", "input_tokens", "tokens = 5", "max_retries=2"):
            self.assertFalse(rules.OUTPUT_RE.search(text), text)

    def test_timeout_pattern(self):
        found, off = check("timeout")["found"], check("timeout")["off"]
        for line in ("client = OpenAI(timeout=20.0)", "{ timeout: 20_000 }", "request_timeout=30",
                     "REQUEST_TIMEOUT_S = 20", "signal: AbortSignal.timeout(20000),",
                     "await asyncio.wait_for(call(), 20)", '"timeout": 30,'):
            self.assertTrue(found.search(line), line)
        for line in ("setTimeout(() => done(), 50);", "except TimeoutError:", "if timeout == 0:",
                     "raise APITimeoutError(request)"):
            self.assertFalse(found.search(line), line)
        self.assertTrue(off.search("client = OpenAI(timeout=None)"))
        self.assertFalse(off.search("client = OpenAI(timeout=20)"))

    def test_retry_patterns(self):
        retry = check("retry")
        self.assertTrue(retry["found"].search("OpenAI({ maxRetries: 3 })"))
        self.assertTrue(retry["found"].search("@backoff.on_exception(backoff.expo, Exception)"))
        self.assertFalse(retry["found"].search("for attempt in range(3):"))
        self.assertTrue(retry["weak"].search("for attempt in range(3):"))
        self.assertTrue(retry["off"].search("Anthropic(max_retries=0)"))
        self.assertFalse(retry["off"].search("Anthropic(max_retries=10)"))

    def test_usage_pattern_does_not_match_limit_names(self):
        usage = check("usage_logging")["found"]
        for line in ("n = response.usage.input_tokens", "u.prompt_tokens", "usage.totalTokens",
                     "meta = response.usage_metadata"):
            self.assertTrue(usage.search(line), line)
        for line in ("max_output_tokens=5", "max_completion_tokens: 5", "maxOutputTokens: 5"):
            self.assertFalse(usage.search(line), line)

    def test_execution_sinks(self):
        for line in ("eval(code)", "exec(model_output)", "os.system(cmd)", "subprocess.run(cmd)",
                     "return execSync(`ls ${dir}`)", "run(cmd, shell=True)", "new Function(body)"):
            self.assertTrue(rules.SINK_RE.search(line), line)
        for line in ("pattern.exec(text)", "model.eval()", "cursor.execute(sql)", "retrieval(q)"):
            self.assertFalse(rules.SINK_RE.search(line), line)


class CliTests(unittest.TestCase):
    def run_main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = rules.main(argv)
        return code, out.getvalue()

    def test_text_listing_names_every_safeguard_pattern(self):
        code, text = self.run_main([])
        self.assertEqual(code, 0)
        for check_id in ("timeout", "retry", "rate_limit", "fallback", "usage_logging", "tracing",
                         "output_limit", "hardcoded_keys", "untrusted_input", "error_handling"):
            self.assertIn(check_id, text)

    def test_json_listing_is_valid_and_complete(self):
        code, text = self.run_main(["--json"])
        data = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual(len(data["call_patterns"]), len(rules.CALL_PATTERNS))
        self.assertEqual(sorted(data["sdk_imports"]), ["js", "py"])

    def test_unknown_option_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_main(["--nope"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
