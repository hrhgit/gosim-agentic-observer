/**
 * The submission page shows the organizer's decision_replay.html in a sandboxed iframe next to the
 * observed-sky map. Both have a play button and a scrubber over the same run, so they share one
 * position: the number of report actions shown so far (the sky map's cursor). The replay's round k
 * (0-based) is report action k, i.e. cursor k + 1.
 *
 * The replay has no postMessage support of its own, and replays already stored in the results bucket
 * cannot be regenerated, so the page injects a small bridge (and a layout patch for replays rendered
 * before the template's narrow-width fix) into the HTML before handing it to the iframe.
 */

export const REPLAY_POSITION = 'hs26-replay:position'
export const REPLAY_SEEK = 'hs26-replay:seek'
/** Playback settings from the page: `{ speed?, play?, loop? }` (the home-page demo autoplays and loops). */
export const REPLAY_PLAYBACK = 'hs26-replay:playback'
export interface ReplayPlayback { speed?: number; play?: boolean; loop?: boolean }

/** `seq` is the last seek the replay had applied when it sent this: reports older than the page's latest seek are stale. */
export interface ReplayPositionMessage { type: typeof REPLAY_POSITION; round: number; rounds: number; playing: boolean; seq: number }

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** Sky-map cursor (actions shown, 0…actions) for the replay's 0-based round. */
export function cursorForRound(round: number, actions: number): number {
  if (!(actions > 0)) return 0
  return clamp(Math.floor(round) + 1, 1, actions)
}

/** Replay round (0-based) to show for a sky-map cursor. Cursor 0 ("before the first decision") shows round 0. */
export function roundForCursor(cursor: number, rounds: number): number {
  if (!(rounds > 0)) return 0
  return clamp(Math.floor(cursor) - 1, 0, rounds - 1)
}

/** The replay's own report of where it is, or null for any other message. */
export function readPositionMessage(data: unknown): ReplayPositionMessage | null {
  const d = data as Partial<ReplayPositionMessage> | null
  if (!d || typeof d !== 'object' || d.type !== REPLAY_POSITION) return null
  if (!Number.isFinite(d.round) || !Number.isFinite(d.rounds)) return null
  return { type: REPLAY_POSITION, round: Number(d.round), rounds: Number(d.rounds), playing: Boolean(d.playing), seq: Number(d.seq) || 0 }
}

/** A report sent before the replay applied the page's latest seek (it was still playing on) must not move the page back. */
export const isStaleReport = (msg: ReplayPositionMessage, lastSeek: number): boolean => msg.seq < lastSeek

/**
 * Runs inside the replay after its own script. `index`, `playing`, `speed`, `lastStep`, `DATA` and
 * `updateUI` are the replay's globals: every repaint reports the round, and a seek from the page moves
 * to it and pauses, without reporting back. Nothing is reported on load: the page seeks the replay to
 * the shared position once the frame has loaded. A playback message sets the speed, starts or stops,
 * and with `loop` starts over a moment after the last round.
 */
const BRIDGE = `(function(){
if(typeof updateUI!=='function'||typeof DATA==='undefined'||window.parent===window)return;
var paint=updateUI,quiet=false,seq=0,loop=false,hold=0,last=DATA.rounds.length-1;
function button(){return document.getElementById('play')}
function setPlaying(on){playing=on;lastStep=performance.now();var b=button();if(b)b.textContent=on?'\u2161 PAUSE':'\u25B6 PLAY'}
function report(){if(!quiet)parent.postMessage({type:'${REPLAY_POSITION}',round:index,rounds:DATA.rounds.length,playing:playing,seq:seq},'*')}
updateUI=function(){
  paint.apply(this,arguments);report();
  var b=button();
  if(loop&&!playing&&index===last&&b&&/REPLAY/.test(b.textContent)&&!hold)
    hold=setTimeout(function(){hold=0;if(loop&&!playing&&index===last){index=0;setPlaying(true);updateUI()}},2500);
};
window.addEventListener('message',function(e){
  var d=e.data;if(e.source!==parent||!d)return;
  if(d.type==='${REPLAY_PLAYBACK}'){
    if(Number(d.speed)>0){speed=Number(d.speed);var s=document.getElementById('speed');if(s)s.value=String(d.speed);lastStep=performance.now()}
    if(typeof d.loop==='boolean')loop=d.loop;
    if(d.play===true&&!playing){if(index===last)index=0;setPlaying(true);updateUI()}
    else if(d.play===false&&playing){setPlaying(false);updateUI()}
    return;
  }
  if(d.type!=='${REPLAY_SEEK}')return;
  seq=Math.max(seq,Number(d.seq)||0);var i=Math.max(0,Math.min(last,Math.floor(Number(d.round))||0));
  if(playing)setPlaying(false);
  index=i;quiet=true;try{paint()}finally{quiet=false}
});
})();`

/**
 * Narrow-iframe layout for replays rendered before the template fix: under 1080px the old template
 * floated the decision panel over the sky view. Mirrors challenge/templates/decision_replay.html.
 */
export const REPLAY_LAYOUT_PATCH = `@media(max-width:1080px){
main{grid-template-columns:minmax(160px,190px) minmax(0,1fr) minmax(190px,230px);gap:10px;padding:10px}
.right{position:relative;right:auto;top:auto;width:auto;max-height:none;z-index:auto}
.panel{padding:14px}.decision-action{font-size:30px;margin-bottom:14px}.kv{grid-template-columns:66px 1fr}
.legend{right:18px;flex-wrap:wrap;gap:6px 12px}
#dome-caption{top:50px;left:18px;right:auto;bottom:auto;text-align:left}
}
@media(max-width:720px){
html,body{height:auto;overflow:auto}
#app{height:auto;min-height:100%;grid-template-rows:auto auto auto;grid-template-columns:minmax(0,1fr)}
header{flex-wrap:wrap;gap:8px 14px;padding:10px 14px}.header-rule{display:none}
.brand{flex-wrap:wrap;min-width:0}.subtitle{line-height:1.5;overflow-wrap:anywhere}
main{grid-template-columns:minmax(0,1fr)}
#sky-wrap{height:clamp(260px,70vw,420px);order:-1}
footer{grid-template-columns:auto minmax(0,1fr);gap:10px 14px;padding:12px 14px;position:sticky;bottom:0;z-index:4}
.progress-block{grid-column:1/-1;justify-content:space-between}
}`

/** Add the sync bridge and the layout patch to a stored replay page. */
export function embedReplayHtml(html: string): string {
  const style = `<style data-hs26-embed>${REPLAY_LAYOUT_PATCH}</style>`
  const script = `<script data-hs26-embed>${BRIDGE}</script>`
  let out = /<\/head>/i.test(html) ? html.replace(/<\/head>/i, `${style}</head>`) : style + html
  const at = out.toLowerCase().lastIndexOf('</body>')
  out = at >= 0 ? out.slice(0, at) + script + out.slice(at) : out + script
  return out
}
