"""Run the reply prompt against the golden tickets.

Calls the real model, so it needs the same environment variables as the app.
Usage, from the project root: python3 -m evals.run_evals
"""
import json
import sys
from pathlib import Path

from support_bot.reply import draft_reply


def main():
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    failures = 0
    for case in cases:
        reply, ok = draft_reply(case["ticket"])
        lowered = reply.lower()
        missing = [word for word in case["must_include"] if word.lower() not in lowered]
        leaked = [word for word in case["must_not_include"] if word.lower() in lowered]
        passed = ok and not missing and not leaked
        failures += 0 if passed else 1
        print("%s  %s  missing=%s leaked=%s" % ("PASS" if passed else "FAIL", case["name"],
                                                missing, leaked))
    print("%d of %d cases passed" % (len(cases) - failures, len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
