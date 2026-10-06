/**
 * Shared sky-map drawing for the hero console and the submission "observed universe" map.
 * Pure canvas 2D, no chart library. Altitude / LST follow the scorer's formulas exactly.
 * Tiles carry the v3 scheduling class (REQUIRED drawn as a diamond, FLEXIBLE as a square);
 * observed marks are coloured by the scorer's action outcome.
 */
import { OUTCOME_COLORS, type OutcomeClass } from './report'

export type SchedulingClass = 'R' | 'F'
export interface SkyTile { id: string; ra: number; dec: number; cls: SchedulingClass; region: string; exp?: number }
export interface SkySite { lat: number; lon: number; min_alt: number }
export interface ObservedMark { state: OutcomeClass; doneSec: number }
export interface SkyFrame {
  /** Replay time (unix seconds) used for the meridian line and visibility rings; null hides both. */
  nowSec: number | null
  observed: Map<string, ObservedMark>
  /** Length (in replay seconds) of the glow after a tile fills in; 0 disables the pulse. */
  pulseSeconds?: number
  /**
   * How solid to draw the things that mark "now" — the meridian and the reach rings. Defaults to 1.
   * The replay drops it to 0 while it cuts across skipped hours, so the cursor dissolves rather than
   * appearing to leap to a new hour angle.
   */
  timeFade?: number
}

export const CLASS_COLORS: Record<SchedulingClass, string> = { R: '#f5f5f5', F: '#78a6ff' }
/** A point source on the sky, landed on by one fibre of some pointing; the demo console's unit, replacing the fixed-patch "tile". */
export interface SkyTarget { id: string; ra: number; dec: number; required: boolean }
/** The current pointing being drawn live: its sky-average centre and the targets its fibres are landing on. */
export interface LivePointing { ra: number; dec: number; targets: string[] }
const RA_MAX = 360, DEC_MIN = -10, DEC_MAX = 70
export const PAD = { left: 30, right: 10, top: 16, bottom: 18 }
export interface DecBounds { min: number; max: number }

/** Dec extent that frames a set of points with a small margin, so a southern (or otherwise
 * off-centre) footprint isn't squashed against one edge of the fixed -10°..+70° default band. */
function decBoundsOf(points: { dec: number }[]): DecBounds {
  if (!points.length) return { min: DEC_MIN, max: DEC_MAX }
  let min = Infinity, max = -Infinity
  for (const p of points) { if (p.dec < min) min = p.dec; if (p.dec > max) max = p.dec }
  const margin = Math.max(3, (max - min) * 0.08)
  min -= margin; max += margin
  if (max - min < 20) { const mid = (min + max) / 2; min = mid - 10; max = mid + 10 }
  return { min: Math.max(-90, min), max: Math.min(90, max) }
}

/** Dec grid-line spacing that keeps a handful of labelled lines whatever the (possibly narrow) extent. */
function decTicks(bounds: DecBounds): number[] {
  const span = bounds.max - bounds.min
  const step = span > 50 ? 20 : span > 25 ? 10 : 5
  const start = Math.ceil(bounds.min / step) * step
  const ticks: number[] = []
  for (let d = start; d <= bounds.max + 1e-6; d += step) ticks.push(d)
  return ticks
}

/**
 * RA window for a point set, expressed as a rotation (`cut`, the degree the window wraps at) plus a
 * `[min, max]` span measured from that cut. A catalogue with one real footprint can still have targets
 * scattered near both ends of the raw 0°..360° range (RA wraps), so framing it means finding where on
 * the circle there is *nothing* and cutting there — not just taking the raw min/max, which would treat
 * a cluster straddling the 0° seam as two clusters at opposite edges of the plot.
 */
export interface RaBounds { cut: number; min: number; max: number }
const FULL_RA: RaBounds = { cut: 0, min: 0, max: 360 }

/** Largest empty gap on the RA circle, cut at its middle, with a margin and a floor so a tight cluster
 * still gets a readable window instead of collapsing to a point. */
function raBoundsOf(points: { ra: number }[]): RaBounds {
  if (points.length < 2) return FULL_RA
  const angles = Array.from(new Set(points.map(p => mod(p.ra, 360)))).sort((a, b) => a - b)
  if (angles.length < 2) return FULL_RA
  let maxGap = -1, gapStart = 0
  for (let i = 0; i < angles.length; i++) {
    const a = angles[i]!
    const g = mod(angles[(i + 1) % angles.length]! - a, 360)
    if (g > maxGap) { maxGap = g; gapStart = a }
  }
  if (maxGap < 1) return FULL_RA // targets ring the whole circle; no seam to cut
  const cut = mod(gapStart + maxGap / 2, 360)
  const shifted = points.map(p => mod(p.ra - cut, 360))
  let min = Math.min(...shifted), max = Math.max(...shifted)
  const margin = Math.min(maxGap * 0.25, Math.max(3, (max - min) * 0.06))
  min = Math.max(0, min - margin); max = Math.min(360, max + margin)
  if (max - min < 20) { const mid = (min + max) / 2; min = Math.max(0, mid - 10); max = Math.min(360, mid + 10) }
  return { cut, min, max }
}

/** RA grid-line spacing, mirroring decTicks; returns real RA degrees (post-rotation) to label. */
function raTicks(bounds: RaBounds): number[] {
  const span = bounds.max - bounds.min
  const step = span > 180 ? 30 : span > 90 ? 20 : span > 40 ? 10 : span > 15 ? 5 : 2
  const start = Math.ceil(bounds.min / step) * step
  const ticks: number[] = []
  for (let s = start; s <= bounds.max + 1e-6; s += step) ticks.push(Math.round(mod(s + bounds.cut, 360)))
  return ticks
}

/** Where `ra` falls across the window's plot width (0..1), or null if it's outside the window entirely. */
export function raBoundsFrac(bounds: RaBounds, ra: number): number | null {
  const s = mod(ra - bounds.cut, 360)
  if (s < bounds.min || s > bounds.max) return null
  return (s - bounds.min) / (bounds.max - bounds.min)
}

const DEG = Math.PI / 180
const mod = (a: number, n: number) => ((a % n) + n) % n
/** The RA band label a point falls in — same eight 45°-wide bands the grid draws, R00 at ra=0. */
export const regionOf = (ra: number): string => `R0${Math.floor(mod(ra, 360) / 45)}`

/** Local sidereal time in degrees (GMST + east longitude). */
export function lstDeg(lon: number, unixSeconds: number): number {
  const jd = 2440587.5 + unixSeconds / 86400
  const d = jd - 2451545.0
  const gmst = 280.46061837 + 360.98564736629 * d
  return mod(gmst + lon, 360)
}

/** Altitude of a point (ra, dec in degrees) at a site and time, in degrees. */
export function altitudeDeg(site: SkySite, ra: number, dec: number, unixSeconds: number): number {
  const h = (mod(lstDeg(site.lon, unixSeconds) - ra + 180, 360) - 180) * DEG
  const sinAlt = Math.sin(site.lat * DEG) * Math.sin(dec * DEG) + Math.cos(site.lat * DEG) * Math.cos(dec * DEG) * Math.cos(h)
  return Math.asin(Math.max(-1, Math.min(1, sinAlt))) / DEG
}

export const isVisible = (site: SkySite, tile: SkyTile, unixSeconds: number) => altitudeDeg(site, tile.ra, tile.dec, unixSeconds) >= site.min_alt

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
}

/** Parse a v3 tiles.csv (tile_id, ra_deg, dec_deg, nominal_exptime_seconds, region_id, scheduling_class, ...). */
export function parseTilesCsv(text: string): SkyTile[] {
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter(l => l.trim())
  if (!lines.length) return []
  const header = lines[0]!.split(',').map(h => h.trim())
  const col = (name: string) => header.indexOf(name)
  const [ci, cr, cd, cc, cg, ce] = [col('tile_id'), col('ra_deg'), col('dec_deg'), col('scheduling_class'), col('region_id'), col('nominal_exptime_seconds')]
  if (ci < 0 || cr < 0 || cd < 0) return []
  const out: SkyTile[] = []
  for (const line of lines.slice(1)) {
    const cells = line.split(',')
    const ra = Number(cells[cr]), dec = Number(cells[cd])
    if (!Number.isFinite(ra) || !Number.isFinite(dec)) continue
    const cls = String(cells[cc] ?? '').trim().toUpperCase().startsWith('R') ? 'R' : 'F'
    const region = cg >= 0 ? String(cells[cg] ?? '').trim() : `R${String(Math.floor(mod(ra, 360) / 45)).padStart(2, '0')}`
    out.push({ id: String(cells[ci]).trim(), ra, dec, cls, region, exp: ce >= 0 ? Number(cells[ce]) : undefined })
  }
  return out
}

/** Size the backing store to the CSS box × devicePixelRatio; returns the CSS size. */
export function fitCanvas(canvas: HTMLCanvasElement): { w: number; h: number } {
  const dpr = Math.min(window.devicePixelRatio || 1, 3)
  const w = Math.max(1, canvas.clientWidth), h = Math.max(1, canvas.clientHeight)
  const bw = Math.round(w * dpr), bh = Math.round(h * dpr)
  if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh }
  canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0)
  return { w, h }
}

function tilePath(ctx: CanvasRenderingContext2D, cls: SchedulingClass, cx: number, cy: number, half: number) {
  ctx.beginPath()
  if (cls === 'R') {
    const r = half * 1.45
    ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath()
  } else {
    ctx.rect(cx - half, cy - half, half * 2, half * 2)
  }
}

/** Projection for the RA/Dec plot box; shared by the tile map and the target map so both read identically. */
function plot(canvas: HTMLCanvasElement, dec: DecBounds = { min: DEC_MIN, max: DEC_MAX }) {
  const { w, h } = fitCanvas(canvas)
  const pw = w - PAD.left - PAD.right, ph = h - PAD.top - PAD.bottom
  return { w, h, pw, ph, dec, x: (ra: number) => PAD.left + (ra / RA_MAX) * pw, y: (d: number) => PAD.top + ((dec.max - d) / (dec.max - dec.min)) * ph }
}

/**
 * Graticule and the "now" meridian line, identical on both maps when `ra` is left null (the tile map's
 * fixed 0°..360° band, 8 regions + R00..R07 header). The target map passes its own `RaBounds` instead:
 * a cropped window gets plain degree ticks and no region header, since R00..R07 (tile-map regions) are
 * meaningless once the axis no longer spans the whole circle.
 */
function drawGrid(ctx: CanvasRenderingContext2D, site: SkySite, nowSec: number | null, timeFade: number, geo: ReturnType<typeof plot>, ra: RaBounds | null = null) {
  const { w, pw, ph, x, y, dec } = geo
  ctx.clearRect(0, 0, geo.w, geo.h)
  ctx.font = '10px "IBM Plex Mono", ui-monospace, monospace'
  ctx.textBaseline = 'middle'
  ctx.lineWidth = 1
  ctx.strokeStyle = 'rgba(255,255,255,.12)'
  ctx.fillStyle = 'rgba(255,255,255,.4)'
  if (!ra) {
    for (let d = 0; d <= 360; d += 45) {
      const px = Math.round(x(d)) + .5
      ctx.beginPath(); ctx.moveTo(px, PAD.top); ctx.lineTo(px, PAD.top + ph); ctx.stroke()
      ctx.textAlign = 'center'
      if (d < 360) ctx.fillText(`R0${d / 45}`, x(d + 22.5), PAD.top / 2)
      ctx.fillText(d === 360 ? '360°' : `${d}°`, px, geo.h - PAD.bottom / 2)
    }
  } else {
    ctx.textAlign = 'center'
    for (const d of raTicks(ra)) {
      const px = Math.round(x(d)) + .5
      ctx.beginPath(); ctx.moveTo(px, PAD.top); ctx.lineTo(px, PAD.top + ph); ctx.stroke()
      ctx.fillText(`${d}°`, px, geo.h - PAD.bottom / 2)
    }
  }
  ctx.textAlign = 'right'
  for (const d of decTicks(dec)) {
    const py = Math.round(y(d)) + .5
    ctx.beginPath(); ctx.moveTo(PAD.left, py); ctx.lineTo(PAD.left + pw, py); ctx.stroke()
    ctx.fillText(`${d > 0 ? '+' : ''}${Math.round(d)}°`, PAD.left - 5, py)
  }
  ctx.strokeStyle = 'rgba(255,255,255,.25)'
  ctx.strokeRect(PAD.left + .5, PAD.top + .5, pw - 1, ph - 1)

  if (nowSec != null && timeFade > 0.02) {
    const lst = lstDeg(site.lon, nowSec)
    const frac = ra ? raBoundsFrac(ra, lst) : 0
    if (!ra || frac != null) {
      const px = Math.round(x(lst)) + .5
      ctx.strokeStyle = `rgba(49,94,251,${.75 * timeFade})`
      ctx.setLineDash([3, 3])
      ctx.beginPath(); ctx.moveTo(px, PAD.top); ctx.lineTo(px, PAD.top + ph); ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = `rgba(120,166,255,${timeFade})`
      ctx.textAlign = px > w - 40 ? 'right' : 'left'
      ctx.fillText('LST', px + (px > w - 40 ? -4 : 4), PAD.top + 8)
    } else {
      // LST is off-window right now: a small edge arrow says which way, instead of a line to nowhere.
      const s = mod(lst - ra.cut, 360)
      const onLeft = mod(ra.min - s, 360) <= mod(s - ra.max, 360)
      const ex = onLeft ? PAD.left + 1 : PAD.left + pw - 1, ey = PAD.top + 9
      ctx.fillStyle = `rgba(120,166,255,${timeFade})`
      ctx.beginPath()
      if (onLeft) { ctx.moveTo(ex, ey); ctx.lineTo(ex + 6, ey - 4); ctx.lineTo(ex + 6, ey + 4) }
      else { ctx.moveTo(ex, ey); ctx.lineTo(ex - 6, ey - 4); ctx.lineTo(ex - 6, ey + 4) }
      ctx.closePath(); ctx.fill()
      ctx.textAlign = onLeft ? 'left' : 'right'
      ctx.fillText('LST', ex + (onLeft ? 9 : -9), ey)
    }
  }
}

export function drawSkyMap(canvas: HTMLCanvasElement, tiles: SkyTile[], site: SkySite, frame: SkyFrame): void {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const geo = plot(canvas)
  const { pw, x, y } = geo
  const timeFade = Math.max(0, Math.min(1, frame.timeFade ?? 1))
  drawGrid(ctx, site, frame.nowSec, timeFade, geo)

  const s = Math.max(4, Math.min(9, Math.round(pw / 90)))
  const half = s / 2
  const pulse = frame.pulseSeconds ?? 0
  for (const tile of tiles) {
    const cx = x(mod(tile.ra, 360)), cy = y(tile.dec)
    const outline = CLASS_COLORS[tile.cls]
    const mark = frame.observed.get(tile.id)
    ctx.globalAlpha = 1
    if (frame.nowSec != null && timeFade > 0.02 && isVisible(site, tile, frame.nowSec)) {
      ctx.strokeStyle = `rgba(255,255,255,${.4 * timeFade})`
      ctx.lineWidth = 1
      ctx.beginPath(); ctx.arc(cx, cy, half + 4, 0, Math.PI * 2); ctx.stroke()
    }
    if (mark && mark.state !== 'completed') {
      // interrupted / unsafe / invalid attempts: coloured outline, no fill
      ctx.strokeStyle = OUTCOME_COLORS[mark.state]
      ctx.lineWidth = 1.5
      tilePath(ctx, tile.cls, cx, cy, half)
      ctx.stroke()
      continue
    }
    if (mark) {
      const color = OUTCOME_COLORS.completed
      if (pulse > 0 && frame.nowSec != null) {
        const k = 1 - Math.min(1, Math.max(0, (frame.nowSec - mark.doneSec) / pulse))
        if (k > 0) {
          ctx.save()
          ctx.globalAlpha = k * .9
          ctx.shadowColor = color; ctx.shadowBlur = 10 + 10 * k
          ctx.fillStyle = color
          tilePath(ctx, tile.cls, cx, cy, half + 2 * k)
          ctx.fill()
          ctx.restore()
        }
      }
      ctx.fillStyle = color
      tilePath(ctx, tile.cls, cx, cy, half)
      ctx.fill()
      if (tile.cls === 'R') { ctx.strokeStyle = outline; ctx.lineWidth = 1; ctx.stroke() }
    } else {
      ctx.strokeStyle = outline
      ctx.lineWidth = tile.cls === 'R' ? 1.25 : 1
      ctx.globalAlpha = tile.cls === 'R' ? 1 : .8
      tilePath(ctx, tile.cls, cx, cy, half - .5)
      ctx.stroke()
    }
  }
  ctx.globalAlpha = 1
}

function targetPath(ctx: CanvasRenderingContext2D, required: boolean, cx: number, cy: number, half: number) {
  ctx.beginPath()
  if (required) {
    const r = half * 1.45
    ctx.moveTo(cx, cy - r); ctx.lineTo(cx + r, cy); ctx.lineTo(cx, cy + r); ctx.lineTo(cx - r, cy); ctx.closePath()
  } else {
    ctx.rect(cx - half, cy - half, half * 2, half * 2)
  }
}

export interface TargetFrame extends SkyFrame {
  /** The pointing being drawn live, if any — its 15-ish fibre hits are joined to its centre with thin spokes. */
  livePointing?: LivePointing | null
}

/**
 * Current-format sky map: point-source targets (◇ required, □ optional), each lit up once a pointing's
 * fibre lands on it, plus — while a pointing is live — its field centre joined to each of its hits.
 */
/** Bounds are cheap to recompute but the target list is stable for a whole replay, so cache by array identity. */
const targetDecBoundsCache = new WeakMap<SkyTarget[], DecBounds>()
function targetDecBounds(targets: SkyTarget[]): DecBounds {
  let bounds = targetDecBoundsCache.get(targets)
  if (!bounds) { bounds = decBoundsOf(targets); targetDecBoundsCache.set(targets, bounds) }
  return bounds
}
const targetRaBoundsCache = new WeakMap<SkyTarget[], RaBounds>()
/** RA window for a target set — exported so the console can place its own LST hover marker identically. */
export function targetRaBounds(targets: SkyTarget[]): RaBounds {
  let bounds = targetRaBoundsCache.get(targets)
  if (!bounds) { bounds = raBoundsOf(targets); targetRaBoundsCache.set(targets, bounds) }
  return bounds
}

export function drawTargetMap(canvas: HTMLCanvasElement, targets: SkyTarget[], site: SkySite, frame: TargetFrame): void {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const raB = targetRaBounds(targets)
  const geo = plot(canvas, targetDecBounds(targets))
  const { pw, y } = geo
  const x = (ra: number) => PAD.left + ((mod(ra - raB.cut, 360) - raB.min) / (raB.max - raB.min)) * pw
  const timeFade = Math.max(0, Math.min(1, frame.timeFade ?? 1))
  drawGrid(ctx, site, frame.nowSec, timeFade, { ...geo, x }, raB)

  // A catalogue runs to thousands of point sources, not dozens of tiles: dots stay small (and required
  // ones only a touch bigger) so individual hits and fill-in progress read at a glance instead of
  // merging into one blob. Scales down further as the catalogue (or a cramped mobile box) gets denser.
  const normalHalf = Math.max(0.75, Math.min(1.5, 60 / Math.sqrt(Math.max(1, targets.length))))
  const requiredHalf = normalHalf + 0.4
  const pulse = frame.pulseSeconds ?? 0

  for (const target of targets) {
    const cx = x(target.ra), cy = y(target.dec)
    const half = target.required ? requiredHalf : normalHalf
    const outline = target.required ? CLASS_COLORS.R : CLASS_COLORS.F
    const mark = frame.observed.get(target.id)
    ctx.globalAlpha = 1
    if (mark && mark.state !== 'completed') {
      ctx.strokeStyle = OUTCOME_COLORS[mark.state]
      ctx.lineWidth = target.required ? 1 : .75
      targetPath(ctx, target.required, cx, cy, half)
      ctx.stroke()
      continue
    }
    if (mark) {
      const color = OUTCOME_COLORS.completed
      if (pulse > 0 && frame.nowSec != null) {
        const k = 1 - Math.min(1, Math.max(0, (frame.nowSec - mark.doneSec) / pulse))
        if (k > 0) {
          ctx.save()
          ctx.globalAlpha = k * .9
          ctx.shadowColor = color; ctx.shadowBlur = 6 + 6 * k
          ctx.fillStyle = color
          targetPath(ctx, target.required, cx, cy, half + k)
          ctx.fill()
          ctx.restore()
        }
      }
      ctx.fillStyle = color
      targetPath(ctx, target.required, cx, cy, half)
      ctx.fill()
      if (target.required) { ctx.strokeStyle = outline; ctx.lineWidth = .75; ctx.stroke() }
    } else {
      ctx.strokeStyle = outline
      ctx.lineWidth = target.required ? 1 : .75
      ctx.globalAlpha = target.required ? 1 : .8
      targetPath(ctx, target.required, cx, cy, half)
      ctx.stroke()
    }
  }
  ctx.globalAlpha = 1

  // The live pointing — its field centre and the ~16 fibres it is landing this exposure — draws last,
  // in flat colour well above the dim catalogue dots, so it is never lost among a few thousand of them.
  if (frame.livePointing) {
    const { ra, dec, targets: hits } = frame.livePointing
    const byId = new Map(targets.map(t => [t.id, t]))
    const cx = x(ra), cy = y(dec)
    const r = Math.max(5, Math.min(11, pw / 55))
    ctx.strokeStyle = 'rgba(120,166,255,.6)'
    ctx.lineWidth = 1
    for (const id of hits) {
      const t = byId.get(id)
      if (!t) continue
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x(t.ra), y(t.dec)); ctx.stroke()
    }
    ctx.fillStyle = '#e8edff'
    for (const id of hits) {
      const t = byId.get(id)
      if (!t) continue
      ctx.beginPath(); ctx.arc(x(t.ra), y(t.dec), normalHalf + 1.4, 0, Math.PI * 2); ctx.fill()
    }
    ctx.strokeStyle = '#78a6ff'
    ctx.lineWidth = 1.5
    ctx.beginPath(); ctx.moveTo(cx - r * .7, cy); ctx.lineTo(cx + r * .7, cy); ctx.moveTo(cx, cy - r * .7); ctx.lineTo(cx, cy + r * .7); ctx.stroke()
    ctx.setLineDash([2, 2])
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke()
    ctx.setLineDash([])
  }
}
