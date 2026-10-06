/**
 * Offline check of the note reader: run it over the handover notes stored in a local card and print what
 * it extracts. Development tool only; the agent never reads card files.
 *
 *   node dist/tools/handover-extract.js --card <card dir> [--ids V4RQ1001,V4RQ1002] [--limit 5] [--out file.jsonl]
 */
import { readFileSync, writeFileSync } from "node:fs";
import * as path from "node:path";
import { parse_utc, format_utc } from "../core/astro";
import { HandoverReader } from "../handover/reader";
import { SiteCalendar } from "../handover/extract";

function arg(name: string): string | undefined {
  const at = process.argv.indexOf(name);
  return at >= 0 ? process.argv[at + 1] : undefined;
}

async function main(): Promise<void> {
  const card = arg("--card");
  if (!card) throw new Error("--card is required");
  const scenario = JSON.parse(readFileSync(path.join(card, "config/v4_scenario.json"), "utf8"));
  const nights = readFileSync(path.join(card, "public/v4_night_calendar.csv"), "utf8").trim().split("\n").slice(1).map((line) => {
    const cells = line.split(",");
    return [parse_utc(cells[4]), parse_utc(cells[5])] as [number, number];
  });
  const cal: SiteCalendar = { utcOffsetHours: Number(scenario.site.utc_offset_hours), nights };
  const first = JSON.parse(readFileSync(path.join(card, "public/v4_bulletins.jsonl"), "utf8").split("\n")[0]);
  const reader = new HandoverReader(cal, (text) => process.stderr.write(text + "\n"));
  reader.terrainDirections = (first.notices ?? []).filter((n: { event_kind: string }) => n.event_kind === "terrain_obstruction").map((n: { direction: string }) => n.direction);
  const ids = arg("--ids")?.split(","),
    limit = Number(arg("--limit") ?? 1e9),
    batch = Number(arg("--batch") ?? 4);
  const requests = readFileSync(path.join(card, "truth/v4_observation_requests.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line))
    .filter((r) => HandoverReader.isNote(r.reason) && (!ids || ids.includes(r.request_id)))
    .sort((a, b) => parse_utc(a.issued_at_utc) - parse_utc(b.issued_at_utc))
    .slice(0, limit);
  const began = performance.now();
  for (let i = 0; i < requests.length; i += batch) {
    for (const r of requests.slice(i, i + batch)) reader.submit(r.request_id, parse_utc(r.issued_at_utc), r.reason);
    await reader.settle(600);
  }
  const local = (t: number | null) => (t === null ? null : format_utc(t + cal.utcOffsetHours * 3600).replace("Z", "L").slice(5, 16));
  const lines: string[] = [];
  for (const note of reader.store.notes.values()) {
    const row = {
      id: note.src, issued_local: local(note.issued),
      terrain: note.terrain.map((t) => `${t.azLo}+${t.azSpan} ridge=${t.ridge} ok>=${t.okAbove}`),
      windows: note.windows.map((w) => `${local(w.t0)}..${local(w.t1)} ${w.all ? "ALL" : `${w.azLo}+${w.azSpan}`} ${w.kind}/${w.effect}/${w.conf}`),
      nights: note.nights.map(([n, q]) => `${local(nights[n][0])} ${q}`),
      faults: note.faults.map((f) => `${f.kind}/${f.action} onset=${local(f.onset)} level=${f.level}`),
      requests: note.requests.map((r) => `${local(r.arrive)}..${local(r.deadline)}`),
      codebook: note.codebook,
      // Machine-readable copies (UTC) for tools that build or check local evaluation data.
      utc: {
        issued: format_utc(note.issued),
        windows: note.windows.map((w) => ({ t0: format_utc(w.t0), t1: format_utc(w.t1), all: w.all, azLo: w.azLo, azSpan: w.azSpan, kind: w.kind, effect: w.effect, conf: w.conf })),
        faults: note.faults.map((f) => ({ kind: f.kind, action: f.action, onset: f.onset === null ? null : format_utc(f.onset), level: f.level })),
      },
    };
    lines.push(JSON.stringify(row));
  }
  const out = arg("--out");
  if (out) writeFileSync(out, lines.join("\n") + "\n");
  else process.stdout.write(lines.join("\n") + "\n");
  process.stderr.write(JSON.stringify({ seconds: Number(((performance.now() - began) / 1000).toFixed(1)), ...reader.summary() }) + "\n");
}

main().catch((error) => {
  process.stderr.write((error instanceof Error ? error.stack : String(error)) + "\n");
  process.exitCode = 1;
});
