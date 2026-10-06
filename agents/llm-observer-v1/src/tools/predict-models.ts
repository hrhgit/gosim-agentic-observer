/** Public JSONL task calls, preserving ordering and one persistent model worker. */
import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { BaselineModels, ReliabilityInput } from "../models";
export async function predictMain(argv: string[]): Promise<void> {
  const models = new BaselineModels();
  const modelsAt = argv.indexOf("--models");
  const legacyIds = modelsAt >= 0 ? argv[modelsAt + 1]?.split(",") : undefined;
  if (modelsAt >= 0 && (!legacyIds?.length || legacyIds.some(id => !id))) throw new Error("--models requires explicit legacy comparison ids");
  if (argv.includes("--list")) { process.stdout.write(JSON.stringify(models.registry.list()) + "\n"); return; }
  async function processRequest(line: string): Promise<void> {
    if (!line.trim()) return;
    const input = JSON.parse(line);
    let result: unknown;
    if (legacyIds) result = { models: models.predictConditions(legacyIds, input) };
    else if (input.operation) result = await models.worker.request(input.operation, input.input);
    else result = await models.predictReliability(input as ReliabilityInput);
    process.stdout.write(JSON.stringify(result) + "\n");
  }
  try {
    const at = argv.indexOf("--input");
    if (at >= 0) {
      if (!argv[at + 1]) throw new Error("--input needs a path");
      await processRequest(readFileSync(argv[at + 1], "utf8"));
    } else {
      const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
      try { for await (const line of lines) await processRequest(line); }
      finally { lines.close(); }
    }
  } finally { await models.close(); }
}
if (require.main === module) predictMain(process.argv.slice(2)).catch(error => {
  process.stderr.write(String(error) + "\n"); process.exitCode = 1;
});
