/** Note reader checks that need no model: decoding, time and direction arithmetic, validation, fault bookkeeping. */
const test = require("node:test");
const assert = require("node:assert/strict");
const { annotate, cleanText, decodeMorse, decodeCaesar, decodeSolfa } = require("../dist/handover/text.js");
const { factsFromAnswer, resolveArea, codebookLines } = require("../dist/handover/extract.js");
const { FactStore, arcDistance } = require("../dist/facts/store.js");

const utc = (text) => Date.parse(text) / 1000;
// Paranal-like calendar: UTC-4, nights 20:00-05:00 local.
const cal = { utcOffsetHours: -4, nights: [] };
for (let day = 1; day <= 30; day++) cal.nights.push([utc(`2026-10-${String(day).padStart(2, "0")}T00:00:00Z`) + 86400, utc(`2026-10-${String(day).padStart(2, "0")}T09:00:00Z`) + 86400]);
const issued = utc("2026-10-05T23:24:00Z"); // 10/05 19:24 local, a Monday

test("emoticons and zero-width characters do not split numbers", () => {
  assert.equal(cleanText("云底约 １８,(；・∀・)０００ 英尺"), "云底约 18,000 英尺");
  assert.equal(cleanText("１９：(╬ Ò﹏Ó)３０左右"), "19:30左右");
  assert.equal(cleanText("长​期预报（UTC）"), "长期预报(UTC)");
});

test("Morse in any two symbols", () => {
  assert.equal(decodeMorse("tiTATATATA TATATATATA TAtitiTAti tiTATATATA TATATATATA / TATATA TAtiTA"), "10/10 OK");
  assert.equal(decodeMorse("●○○○○ ●○○●○ ●●○○○ / ●●● ●○●"), "6/7 OK");
  assert.equal(decodeMorse("今晚 有 云"), null);
});

test("Caesar shift is found from the text itself", () => {
  const decoded = decodeCaesar("Zuifyj (qfyjxy): bmtqj xpd zsijw ymns hqtzi, 10/8 21:00-10/9 1:30 qthfq, qtsl-wfslj. Xynqq tgxjwafgqj.");
  assert.equal(decoded.shift, 5);
  assert.match(decoded.text, /whole sky under thin cloud, 10\/8 21:00-10\/9 1:30 local/);
  assert.equal(decodeCaesar("Update (latest): the whole sky has thin cloud; still observable."), null);
});

test("sol-fa digits", () => {
  assert.equal(decodeSolfa("休止 休止 : fa sol"), "00:45");
  assert.equal(decodeSolfa("re re : do sol"), "22:15");
  assert.match(annotate("【立希】♪休止 休止 : fa sol♪ 前拍到 2 个就行。").text, /唱名解码: 00:45/);
});

test("compass ranges", () => {
  assert.deepEqual(resolveArea("E-SE"), { all: false, azLo: 90, azSpan: 45 });
  assert.deepEqual(resolveArea("SW-N"), { all: false, azLo: 225, azSpan: 135 });
  assert.deepEqual(resolveArea("N-SW"), { all: false, azLo: 225, azSpan: 135 });
  assert.equal(resolveArea("ALL").all, true);
  assert.equal(resolveArea("SW").single, true);
  assert.equal(arcDistance(100, 90, 45), 0);
  assert.equal(arcDistance(80, 90, 45), 10);
});

test("calendar times, 'that night' times and corrections of an impossible onset", () => {
  const note = factsFromAnswer({
    windows: [
      { d1: "10/07", t1: "21:00", t2: "01:30", area: "ALL", kind: "thin_cloud", effect: "ok" },
      { d1: "10/10", t1: "23:30", t2: "03:30", night: true, area: "ALL", kind: "maintenance", effect: "closed" },
      { d1: "10/06", t1: "00:00", t2: "03:00", night: true, area: "ALL", kind: "maintenance", effect: "closed" },
      { d1: "10/06", t1: "dusk", t2: "dawn", night: true, area: "E-SE", kind: "thin_cloud", effect: "ok" },
      { d1: "10/08", t1: "05:30", t2: "08:15", utc: true, area: "S-W", kind: "thin_cloud", effect: "ok" },
      { d1: "10/06", t1: "21:00", t2: "22:00", area: "N", kind: "wind", effect: "closed", conf: "rumor" },
      { d1: "13/45", t1: "21:00", t2: "22:00", area: "N", kind: "wind", effect: "closed" },
    ],
    faults: [
      { d: "10/04", t: "03:10", night: true, kind: "instrument", level: 0.5, action: "report" },
      { d: "10/08", t: "03:15", night: true, kind: "scheduled", action: "verify" },
      { d: "10/05", t: "23:00", night: true, kind: "instrument", action: "report" },
      { d: null, t: null, kind: "watch", action: "report" },
    ],
    terrain: [{ dir: "SW", ridge_deg: 37, ok_above_deg: 39 }, { dir: "S-SW", ridge_deg: 52, ok_above_deg: 38 }],
  }, "N1", issued, cal);
  const local = (t) => new Date((t - 4 * 3600) * 1000).toISOString().slice(5, 16);
  assert.deepEqual(note.windows.map((w) => `${local(w.t0)}..${local(w.t1)}`), [
    "10-07T21:00..10-08T01:30", // calendar, wraps past midnight
    "10-10T23:30..10-11T03:30", // the night of the 10th
    "10-07T00:00..10-07T03:00", // "night of the 6th, 00:00-03:00" is the morning of the 7th
    "10-06T20:00..10-07T05:00", // dusk to dawn
    "10-08T01:30..10-08T04:15", // stated in UTC
  ]);
  assert.deepEqual(note.faults.map((f) => (f.onset === null ? null : local(f.onset))), [
    "10-05T03:10", // last night at 3:10
    "10-08T03:15", // an announced job keeps its calendar date
    "10-04T23:00", // a drop already seen cannot be four hours ahead
    null,
  ]);
  assert.equal(note.faults[3].action, "none");
  assert.equal(note.terrain[1].ridge, null); // 52 was a zenith distance
});

test("ridge heights: the direction's own number wins over a wider statement", () => {
  const store = new FactStore();
  store.apply(factsFromAnswer({ terrain: [{ dir: "SW", ridge_deg: 45 }, { dir: "SE", ridge_deg: 32 }] }, "N1", issued, cal));
  store.apply(factsFromAnswer({ terrain: [{ dir: "SW", ridge_deg: 37, ok_above_deg: 39 }, { dir: "S-SW", ok_above_deg: 38 }] }, "N2", issued + 3600, cal));
  assert.equal(store.ridgeFor(225, 1.5), 38.5);
  assert.equal(store.ridgeFor(135, 1.5), 33.5);
  assert.equal(store.ridgeFor(0, 1.5), null);
});

test("a fault note about an onset before the last correct report is settled", () => {
  const store = new FactStore();
  const first = factsFromAnswer({ faults: [{ d: "10/05", t: "03:10", kind: "instrument", action: "report" }] }, "N1", utc("2026-10-05T07:53:00Z"), cal);
  store.apply(first);
  assert.equal(store.openFaults().length, 1);
  store.reportResult(utc("2026-10-05T07:54:00Z"), true, store.openFaults()[0]);
  // The next evening the same fault is mentioned again, once with its time and once without.
  store.apply(factsFromAnswer({ faults: [{ d: "10/04", t: "03:10", night: true, kind: "instrument", action: "report" }, { d: null, t: null, kind: "instrument", action: "report" }] }, "N2", utc("2026-10-05T23:30:00Z"), cal));
  assert.equal(store.openFaults().length, 0);
  // A later fault is new.
  store.apply(factsFromAnswer({ faults: [{ d: "10/20", t: "02:45", kind: "scheduled", action: "verify" }] }, "N3", utc("2026-10-17T23:30:00Z"), cal));
  assert.equal(store.openFaults().length, 1);
});

test("code-word definitions are copied verbatim for later notes", () => {
  const raw = "【祥子】从今天起交接本用暗号，记好：「暖气又坏了」＝那段时间全天下雨、关顶；「咖啡机没水」＝薄云，照拍。\n【灯】西南那边……大概挡到45度吧。";
  assert.deepEqual(codebookLines(raw), ["「暖气又坏了」= 那段时间全天下雨、关顶", "「咖啡机没水」= 薄云,照拍"]);
  const store = new FactStore();
  store.learnCodebook(codebookLines(raw));
  store.learnCodebook(codebookLines(raw));
  assert.equal(store.codebook.length, 2);
  // The model's own wording of the code words is used only while nothing has been copied.
  store.apply(factsFromAnswer({ codebook: ["暖气=下雨"] }, "N1", issued, cal));
  assert.equal(store.codebook.length, 2);
});

test("a report that comes back wrong closes every ticket claim made by then; a job announced for later stays open", () => {
  const at = utc("2026-12-08T05:10:00Z");
  const store = new FactStore();
  store.apply(factsFromAnswer({ faults: [{ d: "12/08", t: "00:05", kind: "instrument", action: "report" }] }, "N1", at, cal));
  store.apply(factsFromAnswer({ faults: [{ d: null, t: null, kind: "instrument", action: "report" }, { d: "12/16", t: "01:30", kind: "scheduled", action: "verify" }] }, "N2", at, cal));
  assert.equal(store.openFaults().length, 3);
  store.reportResult(at + 60, false, store.faults.find((f) => f.src === "N1"));
  assert.deepEqual(store.openFaults().map((f) => f.kind), ["scheduled"]);
});

test("which whole-sky statements count as a stop", () => {
  const at = utc("2027-01-29T03:00:00Z");
  const window = (kind, conf, t0, t1) => ({ t0, t1, all: true, azLo: 0, azSpan: 360, kind, effect: "closed", conf, issued: at, src: kind });
  const store = new FactStore();
  store.apply({ src: "N1", issued: at, terrain: [], faults: [], nights: [], requests: [], codebook: [], windows: [
    window("rain", "firm", at + 3600, at + 5 * 3600), // "rain tonight, dome closed": the bulletin decides, not the note
    window("thick_cloud", "firm", at + 3600, at + 5 * 3600),
    window("other", "low", at + 6 * 3600, at + 9 * 3600), // "we are stopped tonight", from the unreliable writer
    window("power", "firm", at + 86400, at + 5 * 86400),
  ] });
  assert.equal(store.siteClosure(at + 2 * 3600), null);
  assert.deepEqual(store.siteClosure(at + 7 * 3600), { until: at + 9 * 3600, kind: "other" });
  assert.equal(store.nextSiteClosure(at, 8 * 3600).kind, "other");
  // Only a firm stop with a stated cause is taken out of the season's visibility.
  assert.deepEqual(store.closedIntervals(at), [[at + 86400, at + 5 * 86400]]);
});

const { Sky, Terrain } = require("../dist/core/conditions.js");
const scoring ={ program: { bands: { DARK: 0.65, BRIGHT: 0.4 }, multipliers: { DARK: 1.2, BRIGHT: 1.12, BACKUP: 1.06 }, mismatch_multiplier: 1 }, reporting: { false_report_free_allowance: 2 } };
const bulletin = (notices) => ({ notices: notices.map(([event_kind, direction]) => ({ event_kind, direction })) });
const sector = (azLo, azSpan, effect, kind = "wind", firm = true) => ({ azLo, azSpan, effect, kind, firm, key: `${azLo}|${azSpan}|${effect}` });

test("a stated ridge height replaces the terrain prior and gives way to contrary feedback", () => {
  const terrain = new Terrain(225);
  assert.ok(terrain.blocked_probability(225, 45) > 0.4); // prior: may block up to 56 degrees
  terrain.claim = 38.5;
  assert.equal(terrain.blocked_probability(225, 45), 0);
  assert.ok(terrain.blocked_probability(225, 36) > 0.9); // below it only the mountain's width is uncertain
  assert.equal(terrain.blocked_probability(300, 36), 0); // far from the mountain's direction
  for (let i = 0; i < 3; i++) terrain.add(225, 44, true, true);
  assert.equal(terrain.claim, null); // blocked three times well above the stated ridge
});

test("the notes say what a directional notice is: closed sector, thin cloud, or nothing", () => {
  const sky = new Sky(scoring);
  sky.bulletin(bulletin([["cold_snap", "NW"], ["overcast", "S"]]), 0);
  const plain_nw = sky.dir_factor(40, 315),
    plain_s = sky.dir_factor(40, 180);
  assert.ok(plain_nw > 0.85); // by the rules a cold snap barely matters
  assert.ok(plain_s < 0.5); // and overcast is taken as heavy
  sky.set_notes({ sectors: [sector(270, 45, "closed"), sector(180, 45, "ok", "thin_cloud")], all: [] });
  // "That direction is shut for wind": the sector is the notice's direction +-30 degrees, at every altitude.
  assert.equal(sky.dir_factor(40, 300), 0);
  assert.equal(sky.dir_factor(78, 340), 0);
  assert.ok(Math.abs(sky.dir_factor(40, 275) - 0.15) < 1e-9); // 40 degrees from north-west, inside the stretch the note named
  assert.ok(Math.abs(sky.dir_factor(40, 355) - 0.65) < 1e-9); // 40 degrees away on the side the note did not name
  assert.equal(sky.dir_factor(40, 60), 1); // elsewhere untouched
  assert.ok(sky.dir_factor(40, 180) > 0.75); // "thin cloud, keep shooting"
  // A note about wind does not explain a cloud notice, nor the other way round.
  sky.set_notes({ sectors: [sector(180, 45, "closed"), sector(270, 45, "ok", "thin_cloud")], all: [] });
  assert.equal(sky.dir_factor(40, 180), plain_s);
  assert.equal(sky.dir_factor(40, 315), 0, "closed is remembered from the earlier note for this kind of notice");
  // "Too thick to shoot" is heavy dimming, not a closed sector.
  sky.set_notes({ sectors: [sector(180, 45, "closed", "thick_cloud")], all: [] });
  assert.ok(sky.dir_factor(40, 180) > 0.3 && sky.dir_factor(40, 180) < 0.6);
  // The same kind of notice later, without a note: closed is remembered, since every note so far said so.
  sky.bulletin(bulletin([["cold_snap", "E"]]), 900);
  sky.set_notes({ sectors: [], all: [] });
  assert.equal(sky.dir_factor(35, 90), 0);
  assert.equal(sky.dir_factor(35, 150), 1);
  // A closed stretch the bulletin does not show is not avoided, and a note's "rain, dome closed" closes nothing by itself.
  sky.bulletin(bulletin([["overcast", "ALL"]]), 1800);
  sky.set_notes({ sectors: [sector(0, 45, "closed")], all: [{ effect: "closed", kind: "rain", firm: true }] });
  assert.equal(sky.dir_factor(40, 20), 1);
  assert.equal(sky.site_closed_now, false);
});
