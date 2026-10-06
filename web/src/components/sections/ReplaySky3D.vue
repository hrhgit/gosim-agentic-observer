<script setup lang="ts">
/**
 * The homepage replay as a 3D scene: the observatory's own sky drawn as a glass dome over the
 * ground, every target placed where it really stood at that moment, the telescope as a beam of
 * light swinging to each pointing, and an inset that zooms into the telescope's field so the 16
 * fibres can be seen landing on targets. Daylight, the Sun, the Moon and cloud are drawn from the
 * run's own clock and weather, so "why is nothing happening" answers itself.
 *
 * The parent (SkyConsole.vue) owns the clock and the frame loop and calls draw() every animation
 * frame; this component owns `three`, which it loads lazily after the page's largest paint, the
 * same way the hero background does (HeroGalaxy.vue). The parent only mounts it when WebGL is
 * available and motion is not reduced, and falls back to the flat map if setup fails.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { replayActions, replaySite, replayTargets } from '../../composables/useReplayClock'
import { LAND_PHASE, SWING_END, lightsTargets } from '../../lib/replayStory'
import { applyMatrix, daylight, equatorialVec, fieldOffset, horizonMatrix, moonIllumination, moonRaDec, separationDeg, sunRaDec, type Vec3 } from '../../lib/sky3d'

export interface Sky3DFrame {
  /** Sky time (unix seconds) and the local sidereal time it implies, degrees. */
  t: number
  lst: number
  /** Actions finished so far — what the lit targets are built from. */
  settled: number
  /** The exposure on screen (index into replayActions) and how far through its beat it is, 0…1. */
  live: { index: number; phase: number } | null
  /** Where the telescope swings from: the previous pointing, or null when it starts parked at the zenith. */
  from: { ra: number; dec: number } | null
  open: boolean
  transp: number
  /** Bumped when a different run is loaded. */
  version: number
}

const props = defineProps<{ highlight?: string | null }>()
const emit = defineEmits<{ unavailable: []; ready: [] }>()
const { t, tf } = useI18n()

const host = ref<HTMLDivElement | null>(null)
const insetCanvas = ref<HTMLCanvasElement | null>(null)
const insetBox = ref<HTMLDivElement | null>(null)
const labelsEl = ref<HTMLDivElement | null>(null)
const popEl = ref<HTMLDivElement | null>(null)
const linkEl = ref<SVGLineElement | null>(null)
const ready = ref(false)
const touch = typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches
/** What the inset caption says: the live exposure's fibre count, or why the telescope is idle. */
const inset = ref<{ mode: 'live' | 'idle' | 'day' | 'closed'; hits: number }>({ mode: 'idle', hits: 0 })
const pop = ref({ key: 0, text: '' })
const insetFoot = computed(() => {
  const m = inset.value.mode
  if (m === 'live') return tf('hero.console.sky3d.inset_live', { n: inset.value.hits })
  return t(`hero.console.sky3d.inset_${m}`)
})

let pending: Sky3DFrame | null = null
let renderFn: ((f: Sky3DFrame) => void) | null = null
let dispose: (() => void) | null = null
let cancelled = false

/** Called by the parent every animation frame. Before the scene exists it only remembers the frame. */
function draw(frame: Sky3DFrame) {
  pending = frame
  renderFn?.(frame)
}
defineExpose({ draw })

const R = 10
const FIELD_DEG = 1.9
const smooth = (x: number) => { const u = Math.max(0, Math.min(1, x)); return u * u * (3 - 2 * u) }
/** How far the fibres have reached out at a point in the beat, 0…1 (they land at LAND_PHASE). */
const reachAt = (phase: number) => Math.max(0, Math.min(1, (phase - SWING_END) / (LAND_PHASE - SWING_END)))

function afterLcp(): Promise<void> {
  return new Promise(resolve => {
    let done = false
    let obs: PerformanceObserver | null = null
    const finish = () => { if (done) return; done = true; obs?.disconnect(); resolve() }
    try {
      obs = new PerformanceObserver(() => finish())
      obs.observe({ type: 'largest-contentful-paint', buffered: true })
    } catch { finish(); return }
    setTimeout(finish, 2500)
  })
}

onMounted(() => {
  const el = host.value
  if (!el) return
  const setup = async () => {
    if (cancelled) return
    let THREE: typeof import('three')
    try { THREE = await import('three') } catch { emit('unavailable'); return }
    if (cancelled || !host.value) return
    let renderer: InstanceType<typeof THREE.WebGLRenderer>
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' })
    } catch { emit('unavailable'); return }
    build(THREE, renderer, el)
    ready.value = true
    emit('ready')
    if (pending) renderFn?.(pending)
  }
  void afterLcp().then(() => {
    if (cancelled) return
    const w = window as unknown as { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number }
    if (typeof w.requestIdleCallback === 'function') w.requestIdleCallback(() => { void setup() }, { timeout: 1500 })
    else setTimeout(() => { void setup() }, 200)
  })
})

onBeforeUnmount(() => { cancelled = true; dispose?.() })

/** A soft round glow, white so materials can tint it. */
function glowTexture(THREE: typeof import('three'), inner = 0.22) {
  const size = 64
  const c = document.createElement('canvas')
  c.width = c.height = size
  const g = c.getContext('2d')!
  const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
  grad.addColorStop(0, 'rgba(255,255,255,1)')
  grad.addColorStop(inner, 'rgba(255,255,255,.85)')
  grad.addColorStop(0.5, 'rgba(255,255,255,.22)')
  grad.addColorStop(1, 'rgba(255,255,255,0)')
  g.fillStyle = grad
  g.fillRect(0, 0, size, size)
  return new THREE.CanvasTexture(c)
}

/** Lit Moon disc for a lit fraction k, bright limb on the right; the sprite is turned to face the Sun. */
function paintMoon(c: HTMLCanvasElement, k: number) {
  const g = c.getContext('2d')!
  const s = c.width, r = s * 0.3, cx = s / 2, cy = s / 2
  g.clearRect(0, 0, s, s)
  const halo = g.createRadialGradient(cx, cy, r * 0.8, cx, cy, s / 2)
  halo.addColorStop(0, `rgba(220,228,255,${0.18 + 0.3 * k})`)
  halo.addColorStop(1, 'rgba(220,228,255,0)')
  g.fillStyle = halo
  g.fillRect(0, 0, s, s)
  g.fillStyle = 'rgba(60,66,82,.9)'
  g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fill()
  g.fillStyle = '#eef2ff'
  g.beginPath()
  g.arc(cx, cy, r, -Math.PI / 2, Math.PI / 2)
  // Terminator: an ellipse whose half-width runs from r (new) through 0 (half) to r (full).
  const w = r * Math.abs(1 - 2 * k)
  g.ellipse(cx, cy, w, r, 0, Math.PI / 2, -Math.PI / 2, k < 0.5)
  g.fill()
}

function build(THREE: typeof import('three'), renderer: InstanceType<typeof import('three').WebGLRenderer>, el: HTMLDivElement) {
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5)
  renderer.setPixelRatio(dpr)
  renderer.setSize(el.clientWidth, el.clientHeight)
  renderer.domElement.className = 'sky3d-canvas'
  el.prepend(renderer.domElement)

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(36, el.clientWidth / Math.max(1, el.clientHeight), 0.5, 400)
  const disposables: { dispose(): void }[] = []
  const keep = <T extends { dispose(): void }>(x: T) => { disposables.push(x); return x }
  const glow = keep(glowTexture(THREE))

  // --- ground, horizon, compass --------------------------------------------------------------
  const groundTex = (() => {
    const c = document.createElement('canvas')
    c.width = c.height = 256
    const g = c.getContext('2d')!
    const grad = g.createRadialGradient(128, 128, 0, 128, 128, 128)
    grad.addColorStop(0, 'rgba(255,255,255,1)')
    grad.addColorStop(0.42, 'rgba(255,255,255,.95)')
    grad.addColorStop(0.5, 'rgba(255,255,255,.55)')
    grad.addColorStop(1, 'rgba(255,255,255,0)')
    g.fillStyle = grad
    g.fillRect(0, 0, 256, 256)
    return keep(new THREE.CanvasTexture(c))
  })()
  const groundNight = new THREE.Color('#141c30'), groundDay = new THREE.Color('#3a4a3e')
  const groundMat = keep(new THREE.MeshBasicMaterial({ map: groundTex, color: groundNight.clone(), transparent: true, depthWrite: true }))
  const ground = new THREE.Mesh(keep(new THREE.CircleGeometry(R * 2.4, 72)), groundMat)
  ground.rotation.x = -Math.PI / 2
  ground.position.y = -0.01
  scene.add(ground)

  const ringPoints = (radius: number, y: number, steps = 128) => {
    const pts: InstanceType<typeof THREE.Vector3>[] = []
    for (let i = 0; i <= steps; i += 1) {
      const a = (i / steps) * Math.PI * 2
      pts.push(new THREE.Vector3(Math.cos(a) * radius, y, Math.sin(a) * radius))
    }
    return pts
  }
  const horizonMat = keep(new THREE.LineBasicMaterial({ color: new THREE.Color('#8fa6d8'), transparent: true, opacity: 0.55 }))
  scene.add(new THREE.Line(keep(new THREE.BufferGeometry().setFromPoints(ringPoints(R, 0.01))), horizonMat))

  // --- the sky dome: glass shell, "too low" band, altitude rings -----------------------------
  const minAlt = (replaySite.min_alt || 30) * Math.PI / 180
  const skyUniforms = {
    uDay: { value: 0 },
    uCloud: { value: 0 },
    uSunDir: { value: new THREE.Vector3(0, -1, 0) },
  }
  const skyMat = keep(new THREE.ShaderMaterial({
    uniforms: skyUniforms,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    vertexShader: `
      varying vec3 vPos; varying vec3 vN; varying vec3 vView;
      void main() {
        vPos = position;
        vec4 world = modelMatrix * vec4(position, 1.0);
        vN = normalize(mat3(modelMatrix) * normal);
        vView = normalize(cameraPosition - world.xyz);
        gl_Position = projectionMatrix * viewMatrix * world;
      }`,
    fragmentShader: `
      uniform float uDay; uniform float uCloud; uniform vec3 uSunDir;
      varying vec3 vPos; varying vec3 vN; varying vec3 vView;
      void main() {
        float h = clamp(vPos.y / ${R.toFixed(1)}, 0.0, 1.0);
        vec3 night = mix(vec3(0.07, 0.10, 0.22), vec3(0.02, 0.035, 0.09), h);
        vec3 day = mix(vec3(0.66, 0.80, 0.97), vec3(0.24, 0.47, 0.90), h);
        vec3 c = mix(night, day, uDay);
        float toSun = max(dot(normalize(vPos), uSunDir), 0.0);
        float twilight = uDay * (1.0 - uDay) * 4.0;
        c += vec3(1.0, 0.48, 0.18) * twilight * pow(toSun, 3.0) * (1.0 - h) * 0.9;
        c += vec3(1.0, 0.95, 0.8) * uDay * pow(toSun, 24.0) * 0.6;
        float rim = 1.0 - abs(dot(vView, vN));
        float a = mix(0.07, 0.34, uDay) + rim * rim * mix(0.32, 0.2, uDay);
        vec3 cloud = mix(vec3(0.20, 0.22, 0.28), vec3(0.62, 0.66, 0.72), uDay);
        c = mix(c, cloud, uCloud * 0.75);
        a = mix(a, 0.7, uCloud * 0.75);
        gl_FragColor = vec4(c, a);
      }`,
  }))
  const shell = new THREE.Mesh(keep(new THREE.SphereGeometry(R, 72, 36, 0, Math.PI * 2, 0, Math.PI / 2)), skyMat)
  shell.renderOrder = 1
  scene.add(shell)

  const lowMat = keep(new THREE.MeshBasicMaterial({ color: new THREE.Color('#05070d'), transparent: true, opacity: 0.38, depthWrite: false, side: THREE.DoubleSide }))
  const lowBand = new THREE.Mesh(keep(new THREE.SphereGeometry(R * 1.002, 72, 6, 0, Math.PI * 2, Math.PI / 2 - minAlt, minAlt)), lowMat)
  lowBand.renderOrder = 2
  scene.add(lowBand)

  const minAltMat = keep(new THREE.LineDashedMaterial({ color: new THREE.Color('#ffcf8a'), dashSize: 0.35, gapSize: 0.22, transparent: true, opacity: 0.75 }))
  const minAltLine = new THREE.Line(keep(new THREE.BufferGeometry().setFromPoints(ringPoints(R * Math.cos(minAlt) * 1.004, R * Math.sin(minAlt) * 1.004, 160))), minAltMat)
  minAltLine.computeLineDistances()
  scene.add(minAltLine)
  const gridMat = keep(new THREE.LineBasicMaterial({ color: new THREE.Color('#9fb6ff'), transparent: true, opacity: 0.12 }))
  const gridPts: number[] = []
  const alt60 = Math.PI / 3
  ringPoints(R * Math.cos(alt60), R * Math.sin(alt60), 96).forEach((p, i, arr) => { if (i) gridPts.push(arr[i - 1]!.x, arr[i - 1]!.y, arr[i - 1]!.z, p.x, p.y, p.z) })
  for (let k = 0; k < 8; k += 1) {
    const az = (k / 8) * Math.PI * 2
    for (let i = 0; i < 24; i += 1) {
      const a0 = (i / 24) * Math.PI / 2, a1 = ((i + 1) / 24) * Math.PI / 2
      gridPts.push(Math.sin(az) * Math.cos(a0) * R, Math.sin(a0) * R, Math.cos(az) * Math.cos(a0) * R, Math.sin(az) * Math.cos(a1) * R, Math.sin(a1) * R, Math.cos(az) * Math.cos(a1) * R)
    }
  }
  const gridGeo = keep(new THREE.BufferGeometry())
  gridGeo.setAttribute('position', new THREE.Float32BufferAttribute(gridPts, 3))
  scene.add(new THREE.LineSegments(gridGeo, gridMat))

  // --- background stars, far outside the dome; decoration only -------------------------------
  const bgCount = touch ? 700 : 1400
  const bgPos = new Float32Array(bgCount * 3)
  for (let i = 0; i < bgCount; i += 1) {
    const u = Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1
    const v = Math.abs(Math.sin(i * 78.233) * 24634.6345) % 1
    const th = u * Math.PI * 2, y = 0.05 + v * 0.95
    const rr = Math.sqrt(1 - y * y)
    bgPos.set([Math.cos(th) * rr * 90, y * 90, Math.sin(th) * rr * 90], i * 3)
  }
  const bgGeo = keep(new THREE.BufferGeometry())
  bgGeo.setAttribute('position', new THREE.BufferAttribute(bgPos, 3))
  const bgMat = keep(new THREE.PointsMaterial({ size: 1.4, sizeAttenuation: false, color: new THREE.Color('#c9d6ff'), transparent: true, opacity: 0.4, depthWrite: false }))
  scene.add(new THREE.Points(bgGeo, bgMat))

  // --- the observatory ------------------------------------------------------------------------
  const ambient = new THREE.AmbientLight(0xb8c6ff, 0.75)
  const sunLight = new THREE.DirectionalLight(0xffffff, 0.0)
  const fill = new THREE.DirectionalLight(0x9db8ff, 0.55)
  fill.position.set(4, 6, 8)
  scene.add(ambient, sunLight, fill)
  const obs = new THREE.Group()
  const wallMat = keep(new THREE.MeshLambertMaterial({ color: new THREE.Color('#7d879c') }))
  const domeMat = keep(new THREE.MeshLambertMaterial({ color: new THREE.Color('#e4e8f2') }))
  const base = new THREE.Mesh(keep(new THREE.CylinderGeometry(0.85, 0.9, 0.7, 32)), wallMat)
  base.position.y = 0.35
  const cap = new THREE.Mesh(keep(new THREE.SphereGeometry(0.85, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2)), domeMat)
  cap.position.y = 0.7
  const slitMat = keep(new THREE.MeshBasicMaterial({ color: new THREE.Color('#0a1022') }))
  const slit = new THREE.Mesh(keep(new THREE.BoxGeometry(0.26, 0.9, 0.12)), slitMat)
  const slitPivot = new THREE.Group()
  slit.position.set(0, 0.42, 0.8)
  slit.rotation.x = -0.55
  slitPivot.position.y = 0.7
  slitPivot.add(slit)
  obs.add(base, cap, slitPivot)
  scene.add(obs)
  const MOUNT = new THREE.Vector3(0, 1.15, 0)

  // --- targets: one Points cloud, placed once in equatorial space and turned by a matrix ------
  const targetUniforms = {
    uDay: { value: 0 },
    uTime: { value: 0 },
    uScale: { value: 1 },
    uMinAlt: { value: Math.sin(minAlt) },
    uMap: { value: glow },
  }
  const targetMat = keep(new THREE.ShaderMaterial({
    uniforms: targetUniforms,
    transparent: true,
    depthWrite: false,
    // Normal blending, not additive: a dense field of a few hundred targets used to sum to a white,
    // noisy smear that read as a rendering fault rather than as stars.
    vertexShader: `
      attribute float aReq; attribute float aDone; attribute float aLive; attribute float aFlash;
      uniform float uDay; uniform float uTime; uniform float uScale; uniform float uMinAlt;
      varying vec3 vColor; varying float vAlpha;
      void main() {
        vec4 world = modelMatrix * vec4(position, 1.0);
        float s = world.y / ${R.toFixed(1)};
        float above = smoothstep(-0.015, 0.02, s);
        float high = smoothstep(uMinAlt - 0.02, uMinAlt + 0.02, s);
        // Seen from outside, the glass dome has a near wall and a far wall; targets on the far wall are seen
        // through the glass and drawn a little fainter, which is enough to tell the two apart.
        float far = 1.0 - smoothstep(-0.3, 0.05, dot(normalize(world.xyz), normalize(cameraPosition - world.xyz)));
        vec3 waiting = mix(vec3(0.42, 0.56, 0.92), vec3(1.0, 0.62, 0.24), aReq);
        vec3 done = mix(vec3(0.80, 0.92, 1.0), vec3(1.0, 0.88, 0.56), aReq);
        float age = uTime - aFlash;
        float flash = age >= 0.0 ? exp(-age * 2.0) : 0.0;
        vec3 c = mix(waiting, done, aDone);
        c = mix(c, vec3(1.0), clamp(flash * 0.8 + aLive * 0.45, 0.0, 1.0));
        float a = mix(0.45 + aReq * 0.2, 0.9, aDone);
        a *= mix(0.35, 1.0, high);
        a *= mix(1.0, 0.6, far);
        a *= mix(1.0, 0.2, uDay);
        a = max(a, max(aLive * 0.95, flash)) * above;
        float size = mix(1.7, 2.2, aDone) + aReq * 0.5 + aLive * 1.4 + flash * 3.0;
        vec4 mv = viewMatrix * world;
        gl_PointSize = size * uScale * (28.0 / -mv.z);
        gl_Position = projectionMatrix * mv;
        vColor = c; vAlpha = a;
      }`,
    fragmentShader: `
      uniform sampler2D uMap;
      varying vec3 vColor; varying float vAlpha;
      void main() {
        float m = texture2D(uMap, gl_PointCoord).a;
        float a = m * vAlpha;
        if (a < 0.01) discard;
        gl_FragColor = vec4(vColor, a);
      }`,
  }))
  const targetsObj = new THREE.Points(new THREE.BufferGeometry(), targetMat)
  targetsObj.matrixAutoUpdate = false
  targetsObj.renderOrder = 3
  targetsObj.frustumCulled = false
  scene.add(targetsObj)
  let done = new Uint8Array(0)
  let aDone: InstanceType<typeof THREE.BufferAttribute> | null = null
  let aLive: InstanceType<typeof THREE.BufferAttribute> | null = null
  let aFlash: InstanceType<typeof THREE.BufferAttribute> | null = null
  let indexOf = new Map<string, number>()
  let eqVecs: Vec3[] = []
  let builtVersion = -1
  let lastSettled = 0
  function buildTargets(version: number) {
    builtVersion = version
    targetsObj.geometry.dispose()
    const n = replayTargets.length
    const pos = new Float32Array(n * 3), req = new Float32Array(n)
    eqVecs = new Array(n)
    indexOf = new Map()
    replayTargets.forEach((tg, i) => {
      const v = equatorialVec(tg.ra, tg.dec)
      eqVecs[i] = v
      pos.set([v.x * R * 0.992, v.y * R * 0.992, v.z * R * 0.992], i * 3)
      req[i] = tg.required ? 1 : 0
      indexOf.set(tg.id, i)
    })
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    geo.setAttribute('aReq', new THREE.BufferAttribute(req, 1))
    aDone = new THREE.BufferAttribute(new Float32Array(n), 1)
    aLive = new THREE.BufferAttribute(new Float32Array(n), 1)
    aFlash = new THREE.BufferAttribute(new Float32Array(n).fill(-100), 1)
    geo.setAttribute('aDone', aDone)
    geo.setAttribute('aLive', aLive)
    geo.setAttribute('aFlash', aFlash)
    targetsObj.geometry = geo
    done = new Uint8Array(n)
    lastSettled = 0
    liveFor = -1
    fieldFor = -1
  }
  /** Mark targets done up to `settled` actions; flash only the ones finished just now. */
  function syncDone(settled: number, now: number) {
    if (!aDone || !aFlash) return
    let changed = false
    if (settled < lastSettled) { done.fill(0); (aDone.array as Float32Array).fill(0); (aFlash.array as Float32Array).fill(-100); lastSettled = 0; changed = true }
    const flash = settled - lastSettled <= 3
    for (let k = lastSettled; k < settled; k += 1) {
      const a = replayActions[k]
      if (!a || !lightsTargets(a)) continue
      for (const id of a.targets) {
        const i = indexOf.get(id)
        if (i == null) continue
        if (!done[i]) { done[i] = 1; (aDone.array as Float32Array)[i] = 1; changed = true }
        if (flash) { (aFlash.array as Float32Array)[i] = now; changed = true }
      }
      if (flash && k === settled - 1 && a.score > 0) showPop(a.score)
    }
    lastSettled = settled
    if (changed) { aDone.needsUpdate = true; aFlash.needsUpdate = true }
  }

  // --- the telescope: a beam to the field, the fibres inside it, a ring where it lands ---------
  const beamUniforms = { uIntensity: { value: 0 }, uColor: { value: new THREE.Color('#7fa8ff') } }
  const beamGeo = keep(new THREE.CylinderGeometry(0.55, 0.05, 1, 40, 1, true))
  beamGeo.translate(0, 0.5, 0)
  const beamMat = keep(new THREE.ShaderMaterial({
    uniforms: beamUniforms,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.AdditiveBlending,
    vertexShader: 'varying float vY; void main(){ vY = position.y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: 'uniform float uIntensity; uniform vec3 uColor; varying float vY; void main(){ float a = (0.05 + 0.24 * vY) * uIntensity; gl_FragColor = vec4(uColor * a, 1.0); }',
  }))
  const beam = new THREE.Mesh(beamGeo, beamMat)
  beam.position.copy(MOUNT)
  beam.renderOrder = 4
  scene.add(beam)
  const coreMat = keep(new THREE.LineBasicMaterial({ color: new THREE.Color('#d6e4ff'), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
  const coreGeo = keep(new THREE.BufferGeometry().setFromPoints([MOUNT, MOUNT]))
  const core = new THREE.Line(coreGeo, coreMat)
  scene.add(core)
  const fibrePos = new Float32Array(16 * 6)
  const fibreGeo = keep(new THREE.BufferGeometry())
  fibreGeo.setAttribute('position', new THREE.BufferAttribute(fibrePos, 3))
  const fibreMat = keep(new THREE.LineBasicMaterial({ color: new THREE.Color('#bcd2ff'), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
  const fibres = new THREE.LineSegments(fibreGeo, fibreMat)
  fibres.frustumCulled = false
  scene.add(fibres)
  const ringMat = keep(new THREE.MeshBasicMaterial({ color: new THREE.Color('#a9c4ff'), transparent: true, opacity: 0, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false }))
  const fieldRing = new THREE.Mesh(keep(new THREE.RingGeometry(0.5, 0.6, 48)), ringMat)
  scene.add(fieldRing)
  const fieldGlowMat = keep(new THREE.SpriteMaterial({ map: glow, color: new THREE.Color('#7fa8ff'), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }))
  const fieldGlow = new THREE.Sprite(fieldGlowMat)
  fieldGlow.scale.setScalar(2.4)
  scene.add(fieldGlow)

  // --- Sun, Moon, cloud ------------------------------------------------------------------------
  const sunMat = keep(new THREE.SpriteMaterial({ map: glow, color: new THREE.Color('#fff1c4'), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }))
  const sun = new THREE.Sprite(sunMat)
  sun.scale.setScalar(3.4)
  scene.add(sun)
  const moonCanvas = document.createElement('canvas')
  moonCanvas.width = moonCanvas.height = 96
  const moonTex = keep(new THREE.CanvasTexture(moonCanvas))
  const moonMat = keep(new THREE.SpriteMaterial({ map: moonTex, transparent: true, depthWrite: false }))
  const moon = new THREE.Sprite(moonMat)
  moon.scale.setScalar(2.2)
  scene.add(moon)
  let moonK = -1

  const cloudTex = (() => {
    const c = document.createElement('canvas')
    c.width = c.height = 128
    const g = c.getContext('2d')!
    for (let i = 0; i < 9; i += 1) {
      const x = 30 + ((i * 37) % 68), y = 44 + ((i * 23) % 40), r = 22 + ((i * 13) % 18)
      const grad = g.createRadialGradient(x, y, 0, x, y, r)
      grad.addColorStop(0, 'rgba(255,255,255,.55)')
      grad.addColorStop(1, 'rgba(255,255,255,0)')
      g.fillStyle = grad
      g.fillRect(0, 0, 128, 128)
    }
    return keep(new THREE.CanvasTexture(c))
  })()
  const clouds = new THREE.Group()
  const cloudMats: InstanceType<typeof THREE.SpriteMaterial>[] = []
  for (let i = 0; i < 11; i += 1) {
    const m = keep(new THREE.SpriteMaterial({ map: cloudTex, color: new THREE.Color('#aab3c4'), transparent: true, opacity: 0, depthWrite: false }))
    cloudMats.push(m)
    const s = new THREE.Sprite(m)
    const az = i * 2.4, alt = 0.45 + ((i * 0.37) % 0.7)
    s.position.set(Math.sin(az) * Math.cos(alt) * R * 0.86, Math.sin(alt) * R * 0.86 + 0.6, Math.cos(az) * Math.cos(alt) * R * 0.86)
    s.scale.set(7.5, 4.2, 1)
    clouds.add(s)
  }
  clouds.renderOrder = 5
  scene.add(clouds)

  // --- camera: holds still so the only motion on screen is the sky and the telescope; drag to turn,
  // wheel to zoom once engaged. (An automatic sway used to turn the whole scene as well, which a
  // first-time visitor could not tell apart from the sky's own rotation.) -----------------------
  const LOOK = new THREE.Vector3(0, R * 0.3, 0)
  const HOME = { theta: -0.55, phi: 1.12 }
  const view = { theta: HOME.theta, phi: HOME.phi, dist: 0, zoom: 1 }
  let resetAnim: { from: typeof view; at: number } | null = null
  const baseDist = () => (camera.aspect < 0.95 ? 42 : camera.aspect < 1.3 ? 35 : 31)
  const placeCamera = () => {
    const d = baseDist() * view.zoom
    camera.position.set(
      LOOK.x + d * Math.sin(view.phi) * Math.sin(view.theta),
      LOOK.y + d * Math.cos(view.phi),
      LOOK.z + d * Math.sin(view.phi) * Math.cos(view.theta),
    )
    camera.lookAt(LOOK)
  }
  const canvas = renderer.domElement
  canvas.style.touchAction = 'pan-y'
  let drag: { x: number; y: number; id: number } | null = null
  let engaged = false
  const onDown = (e: PointerEvent) => {
    drag = { x: e.clientX, y: e.clientY, id: e.pointerId }
    // Keep the drag when the pointer runs off the canvas mid-turn.
    try { canvas.setPointerCapture(e.pointerId) } catch { /* pointer already gone */ }
    engaged = true
    resetAnim = null
  }
  const onMove = (e: PointerEvent) => {
    if (!drag || e.pointerId !== drag.id) return
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y
    drag.x = e.clientX; drag.y = e.clientY
    view.theta -= dx * 0.008
    view.phi = Math.max(0.35, Math.min(1.42, view.phi - dy * 0.006))
  }
  const onUp = () => { drag = null }
  const onLeave = () => { drag = null; engaged = false }
  const onWheel = (e: WheelEvent) => {
    if (!engaged) return
    e.preventDefault()
    view.zoom = Math.max(0.5, Math.min(1.6, view.zoom * Math.exp(e.deltaY * 0.0012)))
  }
  const onDbl = () => { resetAnim = { from: { ...view }, at: performance.now() } }
  canvas.addEventListener('pointerdown', onDown)
  window.addEventListener('pointermove', onMove, { passive: true })
  window.addEventListener('pointerup', onUp, { passive: true })
  window.addEventListener('pointercancel', onUp, { passive: true })
  canvas.addEventListener('pointerleave', onLeave)
  canvas.addEventListener('wheel', onWheel, { passive: false })
  canvas.addEventListener('dblclick', onDbl)

  // --- overlay labels, projected each frame ----------------------------------------------------
  const tmp = new THREE.Vector3()
  let W = el.clientWidth, H = el.clientHeight
  const project = (p: Vec3 | InstanceType<typeof THREE.Vector3>) => {
    tmp.set(p.x, p.y, p.z).project(camera)
    return { x: (tmp.x * 0.5 + 0.5) * W, y: (-tmp.y * 0.5 + 0.5) * H, ok: tmp.z < 1 && tmp.z > -1 }
  }
  const labels = labelsEl.value
  const label = (name: string) => labels?.querySelector<HTMLElement>(`[data-l="${name}"]`) ?? null
  const L = { n: label('n'), e: label('e'), s: label('s'), w: label('w'), sun: label('sun'), moon: label('moon'), alt: label('alt'), obs: label('obs'), field: label('field') }
  /** Labels give way to the inset rather than printing across it. */
  const underInset = (p: { x: number; y: number }) =>
    p.x > insetBoxRect.left - 24 && p.x < insetBoxRect.right + 24 && p.y > insetBoxRect.top - 10 && p.y < insetBoxRect.bottom + 10
  const place = (node: HTMLElement | null, p: { x: number; y: number; ok: boolean } | null, opacity = 1) => {
    if (!node) return
    if (!p || !p.ok || opacity <= 0.02 || underInset(p)) { node.style.opacity = '0'; return }
    node.style.opacity = String(opacity)
    node.style.transform = `translate(${Math.max(14, Math.min(W - 14, p.x)).toFixed(1)}px, ${p.y.toFixed(1)}px)`
  }

  // --- inset: the telescope's field, zoomed in so the 16 fibres can be seen ---------------------
  let fieldFor = -1
  let field: { x: number; y: number; i: number }[] = []
  let fibreAssign: { base: number; x: number; y: number; i: number }[] = []
  let insetMode: 'live' | 'idle' | 'day' | 'closed' = 'idle'
  let insetHits = 0
  let insetRect = { cx: 0, cy: 0, r: 0 }
  let insetBoxRect = { left: 1e9, right: -1e9, top: 1e9, bottom: -1e9 }
  const measureInset = () => {
    const box = insetBox.value, cv = insetCanvas.value
    if (!box || !cv) return
    const hostRect = el.getBoundingClientRect(), r = cv.getBoundingClientRect(), b = box.getBoundingClientRect()
    insetRect = { cx: r.left - hostRect.left + r.width / 2, cy: r.top - hostRect.top + r.height / 2, r: r.width / 2 }
    insetBoxRect = { left: b.left - hostRect.left, right: b.right - hostRect.left, top: b.top - hostRect.top, bottom: b.bottom - hostRect.top }
    const px = Math.round(r.width * (window.devicePixelRatio || 1))
    if (cv.width !== px) { cv.width = px; cv.height = px }
  }
  function prepareField(index: number) {
    fieldFor = index
    const a = replayActions[index]
    field = []
    fibreAssign = []
    if (!a?.center) return
    const { ra, dec } = a.center
    replayTargets.forEach((tg, i) => {
      if (Math.abs(tg.dec - dec) > FIELD_DEG + 0.2) return
      if (separationDeg(ra, dec, tg.ra, tg.dec) > FIELD_DEG) return
      const o = fieldOffset(ra, dec, tg.ra, tg.dec)
      // North up, east to the left: the sky as seen looking up at it.
      field.push({ x: -o.e / FIELD_DEG, y: -o.n / FIELD_DEG, i })
    })
    // Each fibre sits on the rim of the field and reaches in; hand each hit to the nearest free fibre.
    const free = new Set(Array.from({ length: 16 }, (_, k) => k))
    const hits = a.targets.map(id => indexOf.get(id)).filter((i): i is number => i != null)
    const pts = hits.map(i => field.find(f => f.i === i)).filter((f): f is { x: number; y: number; i: number } => !!f)
    pts.sort((p, q) => Math.atan2(p.y, p.x) - Math.atan2(q.y, q.x))
    for (const p of pts) {
      const ang = Math.atan2(p.y, p.x)
      let best = -1, bestD = 1e9
      for (const k of free) {
        const b = (k / 16) * Math.PI * 2 - Math.PI
        const d = Math.abs(Math.atan2(Math.sin(ang - b), Math.cos(ang - b)))
        if (d < bestD) { bestD = d; best = k }
      }
      free.delete(best)
      fibreAssign.push({ base: best, x: p.x, y: p.y, i: p.i })
    }
  }
  function drawInset(phase: number, live: boolean, open: boolean, now: number) {
    const cv = insetCanvas.value
    if (!cv) return
    const g = cv.getContext('2d')
    if (!g) return
    const S = cv.width, c = S / 2, r = S / 2 - 1.5 * (S / 150)
    const k = S / 150
    g.clearRect(0, 0, S, S)
    const bg = g.createRadialGradient(c, c, 0, c, c, r)
    bg.addColorStop(0, 'rgba(14,24,52,.96)')
    bg.addColorStop(1, 'rgba(4,8,20,.96)')
    g.fillStyle = bg
    g.beginPath(); g.arc(c, c, r, 0, Math.PI * 2); g.fill()
    g.strokeStyle = 'rgba(127,168,255,.75)'
    g.lineWidth = 1.2 * k
    g.stroke()
    if (!live || fieldFor < 0) {
      g.strokeStyle = 'rgba(255,255,255,.12)'
      g.lineWidth = 1 * k
      for (let f = 0; f < 16; f += 1) {
        const b = (f / 16) * Math.PI * 2 - Math.PI
        g.beginPath()
        g.moveTo(c + Math.cos(b) * r * 0.93, c + Math.sin(b) * r * 0.93)
        g.lineTo(c + Math.cos(b) * r * 0.84, c + Math.sin(b) * r * 0.84)
        g.stroke()
      }
      return
    }
    // Fade the field in as the beam sets off and out as the beat closes, so one pointing's field never
    // cuts straight to the next one's (or to the idle ring) in a single frame.
    const fade = Math.min(smooth(phase / 0.18), 1 - smooth((phase - 0.9) / 0.1))
    // The field's stars only dip, never vanish: a blank inset every two seconds read as a flicker.
    const dim = 0.35 + 0.65 * fade
    const reach = reachAt(phase)
    const ease = reach * reach * (3 - 2 * reach)
    const scale = r * 0.86
    for (const p of field) {
      const isDone = done[p.i] === 1
      const req = replayTargets[p.i]?.required
      const color = isDone ? (req ? '255,214,128' : '196,226,255') : (req ? '255,150,52' : '110,140,220')
      g.fillStyle = `rgba(${color},${(isDone ? 0.95 : 0.6) * dim})`
      g.beginPath(); g.arc(c + p.x * scale, c + p.y * scale, (isDone ? 2.6 : 2) * k, 0, Math.PI * 2); g.fill()
    }
    const used = new Set(fibreAssign.map(f => f.base))
    g.lineCap = 'round'
    for (let f = 0; f < 16; f += 1) {
      if (used.has(f)) continue
      const b = (f / 16) * Math.PI * 2 - Math.PI
      g.strokeStyle = `rgba(255,255,255,${0.22 * dim})`
      g.lineWidth = 1.2 * k
      g.beginPath()
      g.moveTo(c + Math.cos(b) * r * 0.95, c + Math.sin(b) * r * 0.95)
      g.lineTo(c + Math.cos(b) * r * 0.85, c + Math.sin(b) * r * 0.85)
      g.stroke()
    }
    for (const f of fibreAssign) {
      const b = (f.base / 16) * Math.PI * 2 - Math.PI
      const bx = c + Math.cos(b) * r * 0.95, by = c + Math.sin(b) * r * 0.95
      const tx = c + f.x * scale, ty = c + f.y * scale
      const ex = bx + (tx - bx) * ease, ey = by + (ty - by) * ease
      g.strokeStyle = open ? `rgba(160,196,255,${0.85 * fade})` : `rgba(150,156,170,${0.6 * fade})`
      g.lineWidth = 1.3 * k
      g.beginPath(); g.moveTo(bx, by); g.lineTo(ex, ey); g.stroke()
      g.fillStyle = `rgba(230,240,255,${fade})`
      g.beginPath(); g.arc(ex, ey, 1.8 * k, 0, Math.PI * 2); g.fill()
      if (reach >= 1 && open) {
        const pulse = 0.5 + 0.5 * Math.sin(now * 3 + f.base * 0.4)
        g.strokeStyle = `rgba(255,255,255,${(0.35 + 0.3 * pulse) * fade})`
        g.lineWidth = 1 * k
        g.beginPath(); g.arc(tx, ty, (4.2 + pulse * 1.4) * k, 0, Math.PI * 2); g.stroke()
      }
    }
    if (!open) {
      // Dome shut: the fibres still reach their targets, but no light gets through.
      g.fillStyle = 'rgba(120,128,146,.55)'
      g.beginPath(); g.arc(c, c, r, 0, Math.PI * 2); g.fill()
    }
  }

  // --- the score pop: "+8.6" rising from where the fibres just landed ---------------------------
  const popAt = new THREE.Vector3()
  let popTime = -10
  function showPop(score: number) {
    popAt.copy(fieldRing.position)
    popTime = performance.now() / 1000
    pop.value = { key: pop.value.key + 1, text: `+${(Math.round(score * 10) / 10).toFixed(1)}` }
  }

  // --- per-frame update -----------------------------------------------------------------------
  const m4 = new THREE.Matrix4()
  const dirFrom = new THREE.Vector3(), dirTo = new THREE.Vector3(), dir = new THREE.Vector3()
  const lastDir = new THREE.Vector3(0, 1, 0)
  const up = new THREE.Vector3(0, 1, 0)
  const tip = new THREE.Vector3(0, R, 0)
  let beamLevel = 0
  let cloud = 0
  let dayLevel = 0
  let lastNow = performance.now() / 1000
  let liveFor = -1
  const toVec = (v: Vec3) => new THREE.Vector3(v.x, v.y, v.z)

  renderFn = (f: Sky3DFrame) => {
    const now = performance.now() / 1000
    const dt = Math.min(0.1, now - lastNow)
    lastNow = now
    if (f.version !== builtVersion) buildTargets(f.version)
    const M = horizonMatrix(replaySite.lat, f.lst)
    m4.set(M[0]!, M[1]!, M[2]!, 0, M[3]!, M[4]!, M[5]!, 0, M[6]!, M[7]!, M[8]!, 0, 0, 0, 0, 1)
    targetsObj.matrix.copy(m4)
    targetsObj.matrixWorldNeedsUpdate = true

    // Daylight from the Sun's real altitude; eased so a scrub does not strobe the sky.
    const srd = sunRaDec(f.t)
    const sv = applyMatrix(M, equatorialVec(srd.ra, srd.dec))
    const sunAlt = Math.asin(Math.max(-1, Math.min(1, sv.y))) * 180 / Math.PI
    const dayTarget = daylight(sunAlt)
    dayLevel += (dayTarget - dayLevel) * Math.min(1, dt * 6)
    skyUniforms.uDay.value = dayLevel
    skyUniforms.uSunDir.value.set(sv.x, sv.y, sv.z)
    targetUniforms.uDay.value = dayLevel
    targetUniforms.uTime.value = now
    sun.position.set(sv.x * R * 1.02, sv.y * R * 1.02, sv.z * R * 1.02)
    sunMat.opacity = Math.max(0, Math.min(1, (sv.y + 0.03) * 12))
    sunLight.position.set(sv.x, Math.max(0.1, sv.y), sv.z)
    sunLight.intensity = 1.1 * dayLevel
    ambient.intensity = 0.55 + 0.6 * dayLevel
    groundMat.color.copy(groundNight).lerp(groundDay, dayLevel)
    bgMat.opacity = 0.4 * (1 - dayLevel)

    const mrd = moonRaDec(f.t)
    const mv = applyMatrix(M, equatorialVec(mrd.ra, mrd.dec))
    moon.position.set(mv.x * R * 1.02, mv.y * R * 1.02, mv.z * R * 1.02)
    const k = moonIllumination(f.t)
    if (Math.abs(k - moonK) > 0.02) { moonK = k; paintMoon(moonCanvas, k); moonTex.needsUpdate = true }
    const moonVis = Math.max(0, Math.min(1, (mv.y + 0.02) * 14)) * (1 - 0.5 * dayLevel)
    moonMat.opacity = moonVis

    // Cloud: dome shut means a thick deck; a hazy slot gets a thin veil.
    const cloudTarget = f.open ? Math.max(0, Math.min(0.35, (0.84 - f.transp) * 3)) : 1
    cloud += (cloudTarget - cloud) * Math.min(1, dt * 3)
    skyUniforms.uCloud.value = cloud
    for (const m of cloudMats) m.opacity = cloud * 0.5
    clouds.rotation.y += dt * 0.05

    syncDone(f.settled, now)

    // The live pointing: swing over (first 30% of the beat), then fibres reach out and expose.
    let beamTarget = 0
    if (f.live) {
      const a = replayActions[f.live.index]
      if (a?.center) {
        if (fieldFor !== f.live.index) prepareField(f.live.index)
        dirTo.copy(toVec(applyMatrix(M, equatorialVec(a.center.ra, a.center.dec))))
        if (f.from) dirFrom.copy(toVec(applyMatrix(M, equatorialVec(f.from.ra, f.from.dec))))
        else dirFrom.copy(up)
        const e = smooth(f.live.phase / SWING_END)
        const angle = dirFrom.angleTo(dirTo)
        if (angle < 1e-4) dir.copy(dirTo)
        else {
          // Slerp, so the beam travels across the dome rather than cutting through it.
          const sinA = Math.sin(angle)
          dir.copy(dirFrom).multiplyScalar(Math.sin((1 - e) * angle) / sinA).addScaledVector(dirTo, Math.sin(e * angle) / sinA)
        }
        dir.normalize()
        lastDir.copy(dir)
        beamTarget = f.open ? 1 : 0.35
        const reach = reachAt(f.live.phase)
        const liveNow = reach >= 1 ? f.live.index : -1
        if (liveNow !== liveFor && aLive) {
          ;(aLive.array as Float32Array).fill(0)
          if (liveNow >= 0) for (const id of a.targets) { const i = indexOf.get(id); if (i != null) (aLive.array as Float32Array)[i] = 1 }
          aLive.needsUpdate = true
          liveFor = liveNow
        }
        let n = 0
        for (const id of a.targets) {
          if (n >= 16) break
          const i = indexOf.get(id)
          if (i == null) continue
          const tv = applyMatrix(M, eqVecs[i]!)
          // Full length, faded in: a line growing out of the mount read as a half-drawn glitch.
          fibrePos.set([MOUNT.x, MOUNT.y, MOUNT.z, tv.x * R * 0.99, tv.y * R * 0.99, tv.z * R * 0.99], n * 6)
          n += 1
        }
        for (; n < 16; n += 1) fibrePos.set([0, 0, 0, 0, 0, 0], n * 6)
        fibreGeo.attributes.position!.needsUpdate = true
        fibreMat.opacity = 0.5 * smooth(reach) * (1 - smooth((f.live.phase - 0.9) / 0.1)) * (f.open ? 1 : 0.4)
        insetMode = 'live'
        insetHits = a.targets.length
      }
    } else {
      if (liveFor !== -1 && aLive) { (aLive.array as Float32Array).fill(0); aLive.needsUpdate = true; liveFor = -1 }
      fibreMat.opacity = 0
      insetMode = dayLevel > 0.15 ? 'day' : !f.open ? 'closed' : 'idle'
    }
    if (inset.value.mode !== insetMode || inset.value.hits !== insetHits) inset.value = { mode: insetMode, hits: insetHits }
    beamLevel += (beamTarget - beamLevel) * Math.min(1, dt * (beamTarget > beamLevel ? 10 : 4))
    const beamColor = f.open ? '#7fa8ff' : '#8d96a8'
    beamUniforms.uColor.value.set(beamColor)
    beamUniforms.uIntensity.value = beamLevel
    beam.visible = beamLevel > 0.01
    tip.copy(lastDir).multiplyScalar(R * 0.99)
    beam.scale.set(1, tip.distanceTo(MOUNT), 1)
    beam.quaternion.setFromUnitVectors(up, dir.copy(tip).sub(MOUNT).normalize())
    coreGeo.attributes.position!.setXYZ(1, tip.x, tip.y, tip.z)
    coreGeo.attributes.position!.needsUpdate = true
    coreMat.opacity = 0.75 * beamLevel
    fieldRing.position.copy(tip)
    fieldRing.lookAt(0, 0, 0)
    const pulse = f.live && f.live.phase > LAND_PHASE ? 0.15 * Math.sin(now * 6) : 0
    ringMat.opacity = (0.75 + pulse) * beamLevel
    fieldGlow.position.copy(tip)
    fieldGlowMat.opacity = 0.55 * beamLevel
    slitPivot.rotation.y = Math.atan2(lastDir.x, lastDir.z)
    slitMat.color.set(beamLevel > 0.3 ? '#0a1022' : '#c9cfdc')

    // Camera: still, except for the eased return after a double-click.
    if (resetAnim) {
      const u = Math.min(1, (performance.now() - resetAnim.at) / 700)
      const e = u * u * (3 - 2 * u)
      view.theta = resetAnim.from.theta + (HOME.theta - resetAnim.from.theta) * e
      view.phi = resetAnim.from.phi + (HOME.phi - resetAnim.from.phi) * e
      view.zoom = resetAnim.from.zoom + (1 - resetAnim.from.zoom) * e
      if (u >= 1) resetAnim = null
    }
    placeCamera()
    targetUniforms.uScale.value = (H / 420) * dpr

    renderer.render(scene, camera)

    // Labels follow the scene.
    place(L.n, project({ x: 0, y: 0, z: -R * 1.1 }), 0.8)
    place(L.s, project({ x: 0, y: 0, z: R * 1.1 }), 0.8)
    place(L.e, project({ x: R * 1.1, y: 0, z: 0 }), 0.8)
    place(L.w, project({ x: -R * 1.1, y: 0, z: 0 }), 0.8)
    place(L.sun, sv.y > -0.02 ? project({ x: sv.x * R * 1.02, y: sv.y * R * 1.02 + 1.4, z: sv.z * R * 1.02 }) : null, sunMat.opacity)
    place(L.moon, mv.y > -0.02 ? project({ x: mv.x * R * 1.02, y: mv.y * R * 1.02 + 1.3, z: mv.z * R * 1.02 }) : null, moonVis * 0.9)
    // The minimum-altitude label sits on the dashed ring, on the side facing the viewer.
    const az = view.theta - 0.5
    place(L.alt, project({ x: Math.sin(az) * Math.cos(minAlt) * R, y: Math.sin(minAlt) * R, z: Math.cos(az) * Math.cos(minAlt) * R }), 0.9)
    place(L.obs, project({ x: 0, y: -0.2, z: 0 }), 0.75)
    const tipPx = project(tip)
    const age = now - popTime
    place(L.field, beamLevel > 0.5 ? tipPx : null, Math.min(1, beamLevel))
    const popPx = project(popAt)
    if (popEl.value) {
      if (age < 1.4 && popPx.ok) {
        popEl.value.style.opacity = String(Math.max(0, 1 - age / 1.4))
        // Beside the field, not above it, where the pointing label already sits.
        popEl.value.style.transform = `translate(${popPx.x.toFixed(1)}px, ${(popPx.y - age * 18).toFixed(1)}px)`
      } else popEl.value.style.opacity = '0'
    }
    // The leader line from the field on the dome to the zoomed inset.
    if (linkEl.value) {
      const show = beamLevel > 0.4 && tipPx.ok && insetRect.r > 0
      linkEl.value.style.opacity = show ? String(0.55 * beamLevel) : '0'
      if (show) {
        const dx = tipPx.x - insetRect.cx, dy = tipPx.y - insetRect.cy
        const d = Math.max(1, Math.hypot(dx, dy))
        linkEl.value.setAttribute('x1', tipPx.x.toFixed(1))
        linkEl.value.setAttribute('y1', tipPx.y.toFixed(1))
        linkEl.value.setAttribute('x2', (insetRect.cx + (dx / d) * insetRect.r).toFixed(1))
        linkEl.value.setAttribute('y2', (insetRect.cy + (dy / d) * insetRect.r).toFixed(1))
      }
    }
    drawInset(f.live ? f.live.phase : 0, insetMode === 'live', f.open, now)
  }

  const resize = new ResizeObserver(() => {
    W = el.clientWidth; H = el.clientHeight
    renderer.setSize(W, H)
    camera.aspect = W / Math.max(1, H)
    camera.updateProjectionMatrix()
    measureInset()
  })
  resize.observe(el)
  measureInset()

  dispose = () => {
    renderFn = null
    resize.disconnect()
    canvas.removeEventListener('pointerdown', onDown)
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    window.removeEventListener('pointercancel', onUp)
    canvas.removeEventListener('pointerleave', onLeave)
    canvas.removeEventListener('wheel', onWheel)
    canvas.removeEventListener('dblclick', onDbl)
    targetsObj.geometry.dispose()
    for (const d of disposables) d.dispose()
    renderer.dispose()
    canvas.remove()
  }
}
</script>

<template>
  <div ref="host" class="sky3d" :class="{ 'is-ready': ready, [`hl-${props.highlight}`]: !!props.highlight }">
    <svg class="sky3d-link" aria-hidden="true"><line ref="linkEl" x1="0" y1="0" x2="0" y2="0" /></svg>
    <div ref="labelsEl" class="sky3d-labels" aria-hidden="true">
      <span data-l="n" class="is-compass">{{ t('hero.console.sky3d.north') }}</span>
      <span data-l="e" class="is-compass">{{ t('hero.console.sky3d.east') }}</span>
      <span data-l="s" class="is-compass">{{ t('hero.console.sky3d.south') }}</span>
      <span data-l="w" class="is-compass">{{ t('hero.console.sky3d.west') }}</span>
      <span data-l="sun" class="is-sun">{{ t('hero.console.sky3d.sun') }}</span>
      <span data-l="moon" class="is-moon">{{ t('hero.console.sky3d.moon') }}</span>
      <span data-l="alt" class="is-alt">{{ t('hero.console.sky3d.min_alt') }}</span>
      <span data-l="obs" class="is-obs">{{ t('hero.console.sky3d.observatory') }}</span>
      <span data-l="field" class="is-field">{{ t('hero.console.sky3d.pointing') }}</span>
    </div>
    <div ref="popEl" class="sky3d-pop" aria-hidden="true">{{ pop.text }}</div>
    <div ref="insetBox" class="sky3d-inset" :class="`is-${inset.mode}`">
      <p class="sky3d-inset-title">{{ t('hero.console.sky3d.inset_title') }}</p>
      <canvas ref="insetCanvas" aria-hidden="true"></canvas>
      <p class="sky3d-inset-foot">{{ insetFoot }}</p>
    </div>
    <p class="sky3d-hint" aria-hidden="true">{{ t(touch ? 'hero.console.sky3d.hint_touch' : 'hero.console.sky3d.hint') }}</p>
    <div v-if="!ready" class="sky3d-loading">{{ t('hero.console.sky3d.loading') }}</div>
  </div>
</template>

<style scoped>
.sky3d { position: absolute; inset: 0; overflow: hidden; }
.sky3d :deep(.sky3d-canvas) { position: absolute; inset: 0; width: 100%; height: 100%; display: block; cursor: grab; }
.sky3d :deep(.sky3d-canvas):active { cursor: grabbing; }
.sky3d-link { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.sky3d-link line { stroke: #9ec0ff; stroke-width: 1; stroke-dasharray: 3 3; opacity: 0; }
.sky3d-labels { position: absolute; inset: 0; pointer-events: none; }
.sky3d-labels span {
  position: absolute; left: 0; top: 0; opacity: 0; white-space: nowrap;
  translate: -50% -50%;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .62rem; letter-spacing: .06em;
  color: rgba(214,226,255,.75); text-shadow: 0 0 6px rgba(2,5,12,.9), 0 0 2px rgba(2,5,12,1);
}
.sky3d-labels .is-compass { font-weight: 700; color: rgba(214,226,255,.55); }
.sky3d-labels .is-sun { color: #ffe3a3; }
.sky3d-labels .is-alt { color: #ffcf8a; font-size: .58rem; }
.sky3d-labels .is-obs { translate: -50% 40%; color: rgba(214,226,255,.6); font-size: .58rem; }
.sky3d-labels .is-field { translate: -50% -190%; color: #cfe0ff; font-size: .6rem; }
.sky3d-pop {
  position: absolute; left: 0; top: 0; opacity: 0; translate: 14px -50%; pointer-events: none;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-weight: 700; font-size: .95rem; color: #ffe08a;
  text-shadow: 0 0 10px rgba(255,200,90,.6), 0 0 2px #000;
}
.sky3d-inset {
  position: absolute; right: .6rem; bottom: .55rem; width: clamp(104px, 27%, 156px);
  display: flex; flex-direction: column; align-items: center; gap: .2rem; pointer-events: none;
  transition: opacity .4s;
}
.sky3d-inset canvas { width: 100%; aspect-ratio: 1; display: block; border-radius: 50%; box-shadow: 0 0 0 1px rgba(127,168,255,.25), 0 0 24px rgba(49,94,251,.25); }
.sky3d-inset.is-idle, .sky3d-inset.is-day, .sky3d-inset.is-closed { opacity: .8; }
.sky3d-inset-title, .sky3d-inset-foot {
  margin: 0; text-align: center; line-height: 1.3;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .58rem; letter-spacing: .05em; color: rgba(214,226,255,.7);
  text-shadow: 0 0 4px rgba(2,5,12,1);
}
.sky3d-inset-foot { color: #cfe0ff; font-size: .62rem; }
.sky3d.hl-fibres .sky3d-inset canvas { box-shadow: 0 0 0 2px #78a6ff, 0 0 28px rgba(120,166,255,.7); }
.sky3d-hint {
  position: absolute; left: .7rem; bottom: .55rem; margin: 0; pointer-events: none;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .56rem; letter-spacing: .06em; color: rgba(255,255,255,.38);
}
.sky3d-loading {
  position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .7rem; color: rgba(255,255,255,.45);
}
</style>
