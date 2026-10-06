<script setup lang="ts">
/**
 * One frame of the three-beat decision strip. All three share the same tile
 * grid at the same coordinates — that recurring sky is what makes the frames
 * read as a sequence rather than three unrelated icons.
 *
 * The frames carry no text: captions live outside in the i18n card copy, so a
 * language switch never needs a second drawing.
 */
const props = defineProps<{ panel: 1 | 2 | 3 }>()

const X0 = 40
const Y0 = 44
const CW = 40
const CH = 36
const COLS = 8
const ROWS = 3

// Survey progress: how much of each tile is already in the bag.
const PROGRESS = [
  0.10, 0.04, 0.11, 0.07, 0.03, 0.09, 0.05, 0.08,
  0.05, 0.09, 0.12, 0.06, 0.04, 0.03, 0.07, 0.03,
  0.08, 0.04, 0.07, 0.05, 0.11, 0.06, 0.03, 0.09,
]

const cells = Array.from({ length: COLS * ROWS }, (_, i) => ({
  x: X0 + (i % COLS) * CW,
  y: Y0 + Math.floor(i / COLS) * CH,
  o: PROGRESS[i],
}))

// The two tiles the whole strip turns on.
const A = { x: X0 + 1 * CW, y: Y0 + 0 * CH, cx: X0 + 1.5 * CW, cy: Y0 + 0.5 * CH }
const B = { x: X0 + 5 * CW, y: Y0 + 1 * CH, cx: X0 + 5.5 * CW, cy: Y0 + 1.5 * CH }

// Tiles that are legal to pick right now, flagged in frame 1.
const LEGAL = [A, B, { x: X0 + 3 * CW, y: Y0 + 2 * CH }, { x: X0 + 6 * CW, y: Y0 + 0 * CH }]

const APERTURE = { x: 200, y: 238 }

// Frame 3 swings the slit toward the tile that won.
const slit = (() => {
  if (props.panel !== 3) return { x: 200, y: 234 }
  const dx = A.cx - APERTURE.x
  const dy = A.cy - APERTURE.y
  const len = Math.hypot(dx, dy)
  return { x: 200 + (dx / len) * 18, y: 250 + (dy / len) * 18 }
})()
</script>

<template>
  <!-- The card heading and body right below say what this draws, so announcing
       it again here would only make a screen reader repeat itself. -->
  <svg class="mission-panel" viewBox="0 0 400 300" aria-hidden="true">
    <rect width="400" height="300" fill="#101d29" />

    <!-- Measured conditions, and in frame 1 the forecast running past them. -->
    <g>
      <polyline
        points="40,28 80,22 120,25 160,18 200,21 240,16 280,19 320,24 360,20"
        fill="none" stroke="#f0e9dd" stroke-opacity=".3" stroke-width="1"
      />
      <polyline
        v-if="panel === 1"
        points="200,21 240,16 280,19 320,24 360,20"
        fill="none" stroke="#edb28b" stroke-opacity=".55" stroke-width="1.2" stroke-dasharray="3 3"
      />
      <line x1="40" y1="34" x2="360" y2="34" stroke="#f0e9dd" stroke-opacity=".12" stroke-width="1" />
    </g>

    <!-- The footprint. Fill is progress. -->
    <g>
      <rect
        v-for="(cell, i) in cells" :key="i"
        :x="cell.x" :y="cell.y" :width="CW" :height="CH"
        fill="#f0e9dd" :opacity="cell.o"
      />
    </g>

    <g stroke="#f0e9dd" stroke-opacity=".16" stroke-width="1">
      <line v-for="r in ROWS + 1" :key="`r${r}`" :x1="X0" :y1="Y0 + (r - 1) * CH" :x2="X0 + COLS * CW" :y2="Y0 + (r - 1) * CH" />
      <line v-for="c in COLS + 1" :key="`c${c}`" :x1="X0 + (c - 1) * CW" :y1="Y0" :x2="X0 + (c - 1) * CW" :y2="Y0 + ROWS * CH" />
    </g>

    <!-- Frame 1: what is even legal to pick this slot. -->
    <g v-if="panel === 1" fill="none" stroke="#edb28b" stroke-opacity=".5" stroke-width="1" stroke-dasharray="2 3">
      <rect v-for="(t, i) in LEGAL" :key="i" :x="t.x" :y="t.y" :width="CW" :height="CH" />
    </g>

    <!-- Frame 2: two ways to point, neither committed. Frame 3: one, and it is spent. -->
    <g v-if="panel === 2" stroke="#edb28b" stroke-width="1.2" stroke-dasharray="3 4" fill="none">
      <line class="beam-a" :x1="APERTURE.x" :y1="APERTURE.y" :x2="A.cx" :y2="A.cy" />
      <line class="beam-b" :x1="APERTURE.x" :y1="APERTURE.y" :x2="B.cx" :y2="B.cy" />
    </g>
    <line
      v-if="panel === 3"
      :x1="APERTURE.x" :y1="APERTURE.y" :x2="A.cx" :y2="A.cy"
      stroke="#edb28b" stroke-width="1.6"
    />

    <!-- Candidate A: faint things, only reachable under sky this good. -->
    <g v-if="panel >= 2" :class="panel === 2 ? 'cand-a' : ''">
      <rect :x="A.x" :y="A.y" :width="CW" :height="CH" fill="#edb28b" :opacity="panel === 3 ? .3 : .07" />
      <g fill="#f0e9dd" opacity=".55">
        <circle cx="88" cy="54" r="1.2" /><circle cx="97" cy="50" r="1" />
        <circle cx="106" cy="58" r="1.4" /><circle cx="92" cy="66" r="1" />
        <circle cx="112" cy="68" r="1.2" /><circle cx="101" cy="72" r=".9" />
      </g>
      <rect :x="A.x" :y="A.y" :width="CW" :height="CH" fill="none" stroke="#edb28b" :stroke-width="panel === 3 ? 2.2 : 1.6" />
    </g>

    <!-- Candidate B: one exposure from done. In frame 3 it is what was given up. -->
    <g v-if="panel >= 2" :class="panel === 2 ? 'cand-b' : 'cand-forgone'">
      <rect :x="B.x" :y="B.y" width="34" :height="CH" fill="#edb28b" opacity=".26" />
      <rect :x="B.x" :y="B.y" :width="CW" :height="CH" fill="none" stroke="#edb28b" stroke-width="1.6" />
      <line :x1="B.x + 34" :y1="B.y" :x2="B.x + 34" :y2="B.y + CH" stroke="#edb28b" stroke-width="1" stroke-opacity=".5" stroke-dasharray="2 2" />
    </g>

    <!-- Frame 3: the action carries a reason. -->
    <g v-if="panel === 3">
      <line x1="245" y1="212" x2="214" y2="232" stroke="#edb28b" stroke-opacity=".45" stroke-width="1" />
      <rect x="245" y="194" width="88" height="34" fill="#101d29" stroke="#edb28b" stroke-opacity=".55" stroke-width="1" />
      <g stroke="#f0e9dd" stroke-opacity=".45" stroke-width="2" stroke-linecap="round">
        <line x1="254" y1="203" x2="320" y2="203" />
        <line x1="254" y1="211" x2="308" y2="211" />
        <line x1="254" y1="219" x2="297" y2="219" />
      </g>
    </g>

    <circle v-if="panel === 2" :cx="APERTURE.x" :cy="APERTURE.y" r="3" fill="#edb28b" />

    <!-- The one aperture everything competes for. -->
    <g>
      <line x1="118" y1="266" x2="282" y2="266" stroke="#f0e9dd" stroke-opacity=".22" stroke-width="1" />
      <path d="M180 252 a20 20 0 0 1 40 0 z" fill="#f0e9dd" opacity=".82" />
      <rect x="176" y="252" width="48" height="14" fill="#f0e9dd" opacity=".82" />
      <line x1="200" y1="250" :x2="slit.x" :y2="slit.y" stroke="#101d29" stroke-width="2.5" />
    </g>

    <!-- Frame 3: the slot closes and will not come back. -->
    <g v-if="panel === 3">
      <line x1="118" y1="280" x2="282" y2="280" stroke="#f0e9dd" stroke-opacity=".18" stroke-width="2" />
      <line x1="118" y1="280" x2="282" y2="280" stroke="#edb28b" stroke-width="2" />
    </g>
  </svg>
</template>

<style scoped>
/* Comic panels want a rule around them, not the poster's hard drop shadow —
   three shadows in a row fight each other and break the strip. */
.mission-panel {
  display: block;
  width: 100%;
  height: auto;
  border: 1px solid rgba(240,233,221,.22);
}

/* Weighing, shown rather than described: attention swings between the two. */
@keyframes weigh-a { 0%, 42% { opacity: 1 } 58%, 100% { opacity: .38 } }
@keyframes weigh-b { 0%, 42% { opacity: .38 } 58%, 100% { opacity: 1 } }
.cand-a, .beam-a { animation: weigh-a 3.6s ease-in-out infinite; }
.cand-b, .beam-b { animation: weigh-b 3.6s ease-in-out infinite; }

/* What the decision cost. */
.cand-forgone { opacity: .2; }

@media (prefers-reduced-motion: reduce) {
  .cand-a, .cand-b, .beam-a, .beam-b { animation: none; opacity: 1; }
}
</style>
