import { assertEquals } from "@std/assert";
import { cleanupUploads } from "./observer-cleanup.ts";

Deno.test("cleanup only marks successful scoped staging removal and retries failures", async () => {
  const prefix = "00000000-0000-4000-8000-000000000001/00000000-0000-4000-8000-000000000002/";
  const removed: string[] = [], marked: unknown[] = [];
  const rpc = (name: string, args: Record<string, unknown>) => {
    if (name === "observer_expired_uploads") return Promise.resolve([
      { id: "ok", path: prefix + "source.zip" },
      { id: "retry", path: prefix + "decisions.csv" },
      { id: "invalid", path: "../existing-results/result.zip" },
    ]);
    marked.push(args.p_upload);
    return Promise.resolve(null);
  };
  assertEquals(await cleanupUploads(rpc, (path) => {
    removed.push(path);
    if (path.endsWith("decisions.csv")) throw new Error("temporary network failure");
    return Promise.resolve();
  }), 1);
  assertEquals(removed, [prefix + "source.zip", prefix + "decisions.csv"]);
  assertEquals(marked, ["ok"]);
});
