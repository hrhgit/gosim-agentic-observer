import { assert, assertEquals, assertRejects } from "@std/assert";
import { appendZipEntry, crc32, readZipEntry, singleFileZip, ZipError } from "./observer-zip.ts";
import { agentLogPath, resultCopy, resultCopyPath, storedAgentLog } from "./observer-agent-log.ts";

const encode = (s: string) => new TextEncoder().encode(s);
const decode = (b: Uint8Array | null) => new TextDecoder().decode(b!);

async function unzipList(zip: Uint8Array): Promise<string> {
  // An independent reader: the system unzip must accept the archive and every CRC.
  const file = await Deno.makeTempFile({ suffix: ".zip" });
  try {
    await Deno.writeFile(file, zip);
    const out = await new Deno.Command("unzip", { args: ["-t", file], stdout: "piped", stderr: "piped" }).output();
    assertEquals(out.code, 0, new TextDecoder().decode(out.stdout) + new TextDecoder().decode(out.stderr));
    return new TextDecoder().decode(out.stdout);
  } finally {
    await Deno.remove(file);
  }
}

Deno.test("crc32 matches the ZIP reference value", () => {
  assertEquals(crc32(encode("123456789")), 0xcbf43926);
});

Deno.test("a one-file log archive round-trips, compressed or stored", async () => {
  for (const text of ["", "short", "repeat ".repeat(50000)]) {
    const zip = await singleFileZip("agent.log", encode(text));
    assertEquals(decode(await readZipEntry(zip, "agent.log", 1 << 20)), text);
    assertEquals(await readZipEntry(zip, "other.log", 1 << 20), null);
    await unzipList(zip);
  }
  const zip = await singleFileZip("agent.log", encode("y".repeat(10000)));
  await assertRejects(() => readZipEntry(zip, "agent.log", 100), ZipError);
});

Deno.test("appending keeps every existing entry, the zipball folder and its comment", async () => {
  // Shape of a GitHub zipball: a single top-level folder and a commit comment.
  const base = await Deno.makeTempDir();
  try {
    const root = base + "/ORG-participant-abc-1234567";
    await Deno.mkdir(root);
    await Deno.writeTextFile(root + "/decisions.csv", "night,action\n2026-10-05,wait\n");
    await Deno.writeTextFile(root + "/workflow_result.json", JSON.stringify({ ok: true, pad: "z".repeat(5000) }));
    const zipPath = base + "/result.zip";
    const made = await new Deno.Command("zip", {
      args: ["-r", "-z", zipPath, "ORG-participant-abc-1234567"],
      cwd: base,
      stdin: "piped",
      stdout: "null",
    }).spawn();
    const writer = made.stdin.getWriter();
    await writer.write(encode("0123456789abcdef0123456789abcdef01234567\n.\n"));
    await writer.close();
    assertEquals((await made.status).code, 0);
    const original = await Deno.readFile(zipPath);
    const merged = await appendZipEntry(original, "agent.log", encode("[platform] project stderr\nhello\n"));
    const listing = await unzipList(merged);
    for (const name of ["decisions.csv", "workflow_result.json", "agent.log"]) {
      assertEquals(listing.includes("ORG-participant-abc-1234567/" + name), true, listing);
    }
    assertEquals(
      decode(await readZipEntry(merged, "ORG-participant-abc-1234567/agent.log", 1 << 20)).endsWith("hello\n"),
      true,
    );
    assertEquals(
      decode(await readZipEntry(merged, "ORG-participant-abc-1234567/decisions.csv", 1 << 20)),
      "night,action\n2026-10-05,wait\n",
    );
    // The commit comment of a zipball is preserved.
    assertEquals(decode(merged.slice(-40)), "0123456789abcdef0123456789abcdef01234567");
    await assertRejects(() => appendZipEntry(merged, "agent.log", encode("again")), ZipError);
  } finally {
    await Deno.remove(base, { recursive: true });
  }
});

Deno.test("flat archives get agent.log at the root; malformed archives are refused", async () => {
  const flat = await singleFileZip("decisions.csv", encode("night,action\n"));
  const merged = await appendZipEntry(flat, "agent.log", encode("log"));
  assertEquals(decode(await readZipEntry(merged, "agent.log", 100)), "log");
  await unzipList(merged);
  await assertRejects(() => appendZipEntry(encode("not a zip at all, definitely"), "agent.log", encode("x")), ZipError);
  const truncated = merged.slice(0, merged.length - 30);
  await assertRejects(() => appendZipEntry(truncated, "agent.log", encode("x")), ZipError);
});

Deno.test("the result copy is completed with a stored log and reused for the same result and log", async () => {
  const run = "00000000-0000-4000-8000-000000000002";
  const reference = "github:ORG/runner@" + "a".repeat(40);
  const objects = new Map<string, Uint8Array>();
  const uploads: string[] = [];
  const storage = {
    download: (path: string) => Promise.resolve(objects.get(path) ?? null),
    upload: (path: string, data: Uint8Array) => {
      uploads.push(path);
      objects.set(path, data);
      return Promise.resolve();
    },
    sign: (path: string) => Promise.resolve(objects.has(path) ? "https://storage.test/sign/" + path : null),
  };
  const result = await singleFileZip("decisions.csv", encode("night,action\n"));
  let fetched = 0;
  const source = () => {
    fetched++;
    return Promise.resolve(result);
  };
  assertEquals(await storedAgentLog(run, storage), null);
  objects.set(agentLogPath(run), encode("damaged"));
  assertEquals(await storedAgentLog(run, storage), null);
  objects.set(agentLogPath(run), await singleFileZip("agent.log", encode("stderr line\n")));
  const log = await storedAgentLog(run, storage);
  assertEquals(decode(log), "stderr line\n");
  const copy = await resultCopyPath(run, reference, log);
  assert(copy.startsWith("agent-logs/" + run + "/observer-result-") && copy.endsWith(".zip"));
  // Keyed by the result and the exact log: another commit or a newer log gets its own copy.
  assert(copy !== await resultCopyPath(run, reference, null));
  assert(copy !== await resultCopyPath(run, "github:ORG/runner@" + "b".repeat(40), log));
  assert(copy !== await resultCopyPath(run, reference, encode("stderr line\nmore\n")));
  assertEquals(await resultCopy(run, reference, log, source, storage), "https://storage.test/sign/" + copy);
  const combined = objects.get(copy)!;
  assertEquals(decode(await readZipEntry(combined, "agent.log", 100)), "stderr line\n");
  assertEquals(decode(await readZipEntry(combined, "decisions.csv", 100)), "night,action\n");
  // Downloading again signs the stored copy without reading the result again.
  assertEquals(await resultCopy(run, reference, log, source, storage), "https://storage.test/sign/" + copy);
  assertEquals([fetched, uploads.length], [1, 1]);
  // Without a log the copy is the original result, unchanged.
  const plain = await resultCopy(run, reference, null, source, storage);
  assertEquals(objects.get(plain.replace("https://storage.test/sign/", "")), result);
});
