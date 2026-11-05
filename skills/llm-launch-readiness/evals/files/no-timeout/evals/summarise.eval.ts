// Runs the summary prompt against the golden notes. Calls the real model.
import { readFileSync } from "node:fs";
import { summarise } from "../src/summarise";

type Case = { name: string; notes: string; mustInclude: string[]; mustNotInclude: string[] };

const cases: Case[] = JSON.parse(readFileSync(new URL("./cases.json", import.meta.url), "utf8"));
let failures = 0;

for (const item of cases) {
  const result = await summarise(item.notes);
  const text = result.ok ? result.text.toLowerCase() : "";
  const missing = item.mustInclude.filter((word) => !text.includes(word.toLowerCase()));
  const leaked = item.mustNotInclude.filter((word) => text.includes(word.toLowerCase()));
  const passed = result.ok && missing.length === 0 && leaked.length === 0;
  if (!passed) failures += 1;
  console.log(`${passed ? "PASS" : "FAIL"}  ${item.name}`, { missing, leaked });
}

console.log(`${cases.length - failures} of ${cases.length} cases passed`);
process.exit(failures ? 1 : 0);
