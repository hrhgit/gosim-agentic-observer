#!/usr/bin/env node
/**
 * Does the model's reading survive a change of wording?
 *
 *   node tools/paraphrase_check.cjs [--per-card 4] [--out results/paraphrase-check.json]
 *
 * The notes of cards A1-D1 ask for a fault ticket in four set phrases (提单 / 叫工程组 / 工程组上来 /
 * 打那个电话). A reader that only works for those phrases shows nothing about other cards. This check takes
 * real notes in which the model found such a request, replaces only the request itself by another wording a
 * person could have used, and has the model read the result again. Everything else in the note (who noticed
 * what, since when, that it is not the weather) is left as it was. The rewritten notes are made up here for
 * this test; they are not competition data.
 *
 * Needs the model (LLM_ENV_FILE or the provider variables), the built project (npm run build) and the
 * stored readings of the original notes (results/extract-<card>-off.jsonl, from `npm run notes:extract`).
 */
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
const { parse_utc } = require(path.join(root, "dist/core/astro.js"));
const { HandoverReader } = require(path.join(root, "dist/handover/reader.js"));
const { codebookLines } = require(path.join(root, "dist/handover/extract.js"));

const arg = (name, fallback) => {
  const at = process.argv.indexOf(name);
  return at >= 0 ? process.argv[at + 1] : fallback;
};
const perCard = Number(arg("--per-card", 4));
const cardsRoot = arg("--cards", path.join(root, "../../evaluation_assets/formal-v5-new-four-note-aligned-v2/cards"));

// The set phrases of these cards, and other ways to ask for the same thing.
const REWORDINGS = [
  [/请?按流程提单|现在提单|赶紧提单|提单/g, ["开个工单吧", "走一下故障流程", "该上报了", "记一张故障单"]],
  [/请?把工程组叫上来|麻烦叫一下工程组上来|让工程组上来|叫工程组上来|叫工程组/g, ["找设备组的人来看看", "联系一下维护那边", "请技术支持过来", "喊值班工程师"]],
  [/该打那个电话了|是时候打那个电话了|打那个电话吧|打那个电话/g, ["该找值班工程师了", "得通知维护那边了", "联系技术支持吧", "该上报了"]],
];

function reword(text, salt) {
  let changed = 0, out = text;
  for (const [pattern, options] of REWORDINGS)
    out = out.replace(pattern, () => options[(salt + changed++) % options.length]);
  return { text: out, changed };
}

const isTicket = (fault) => fault.kind === "instrument" && fault.action === "report";

async function main() {
  const rows = [];
  for (const card of ["a", "b", "c", "d"]) {
    const dir = path.join(cardsRoot, card);
    const scenario = JSON.parse(fs.readFileSync(path.join(dir, "config/v4_scenario.json"), "utf8"));
    const nights = fs.readFileSync(path.join(dir, "public/v4_night_calendar.csv"), "utf8").trim().split("\n").slice(1)
      .map((line) => line.split(",")).map((cells) => [parse_utc(cells[4]), parse_utc(cells[5])]);
    const cal = { utcOffsetHours: Number(scenario.site.utc_offset_hours), nights };
    const notes = fs.readFileSync(path.join(dir, "truth/v4_observation_requests.jsonl"), "utf8").trim().split("\n").map((line) => JSON.parse(line))
      .filter((r) => HandoverReader.isNote(r.reason)).sort((a, b) => parse_utc(a.issued_at_utc) - parse_utc(b.issued_at_utc));
    const stored = new Map(fs.readFileSync(path.join(root, "results", `extract-${card}-off.jsonl`), "utf8").trim().split("\n")
      .map((line) => JSON.parse(line)).map((row) => [row.id, row]));
    const reader = new HandoverReader(cal, () => {});
    if (!reader.enabled) throw new Error("no model available: set LLM_ENV_FILE or a provider key");
    const first = JSON.parse(fs.readFileSync(path.join(dir, "public/v4_bulletins.jsonl"), "utf8").split("\n")[0]);
    reader.terrainDirections = (first.notices ?? []).filter((n) => n.event_kind === "terrain_obstruction").map((n) => n.direction);
    // The code words the notes define, as a run would have copied them by then.
    for (const note of notes) reader.store.learnCodebook(codebookLines(note.reason));
    // Notes in which the model found a ticket request and whose text uses one of the set phrases.
    const picked = notes.filter((note) => (stored.get(note.request_id)?.utc.faults ?? []).some(isTicket) && reword(note.reason, 0).changed > 0);
    const step = Math.max(1, Math.floor(picked.length / perCard));
    const batch = picked.filter((_, i) => i % step === 0).slice(0, perCard).map((note, index) => {
      const { text } = reword(note.reason, index), before = note.reason.split("\n");
      return { card, note: note.request_id, id: `REWORDED-${card}-${note.request_id}`, issued: parse_utc(note.issued_at_utc), text,
        reworded: text.split("\n").filter((line, i) => line !== before[i]).map((line) => line.slice(0, 120)) };
    });
    for (let i = 0; i < batch.length; i += 2) {
      for (const item of batch.slice(i, i + 2)) reader.submit(item.id, item.issued, item.text);
      await reader.settle(180);
    }
    for (const item of batch) {
      const facts = reader.store.notes.get(item.id);
      item.model_answered = facts !== undefined;
      item.model_ticket = item.model_answered && facts.faults.some(isTicket);
      item.model_faults = item.model_answered ? facts.faults.map((f) => `${f.kind}/${f.action}`) : [];
      delete item.text;
      rows.push(item);
    }
  }
  const count = (key) => rows.filter((row) => row[key]).length;
  const summary = { notes: rows.length, ticket_in_the_original_reading: rows.length, model_answered: count("model_answered"), ticket_in_the_reworded_reading: count("model_ticket") };
  const out = arg("--out");
  if (out) fs.writeFileSync(out, JSON.stringify({ summary, rows }, null, 1) + "\n");
  for (const row of rows)
    console.log(`${row.card.toUpperCase()} ${row.note}: ${row.model_answered ? (row.model_ticket ? "ticket" : "NO TICKET") : "no answer"} ${JSON.stringify(row.model_faults)} | ${row.reworded[0] ?? ""}`);
  console.log(JSON.stringify(summary));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack : String(error));
  process.exitCode = 1;
});
