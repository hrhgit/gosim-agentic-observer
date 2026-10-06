<script setup lang="ts">
/** Real-3D backdrop for the hero: a slowly turning celestial sphere of survey tiles,
 *  an RA/Dec grid, a soft Milky Way band, footprint rings, a sweeping telescope
 *  reticle, and pointer parallax. Three.js loads lazily during browser idle time so
 *  first paint is never blocked; the scene pauses off-screen, when the tab hides,
 *  and renders a single still frame under prefers-reduced-motion. */
import { onBeforeUnmount, onMounted, ref } from 'vue'

const host = ref<HTMLDivElement | null>(null)
let dispose: (() => void) | null = null
let idleHandle: number | null = null
let idleTimer: ReturnType<typeof setTimeout> | null = null
let lcpObserver: PerformanceObserver | null = null
let lcpFallbackTimer: ReturnType<typeof setTimeout> | null = null
let cancelled = false
const LCP_FALLBACK_MS = 2500

/** Resolves once the page's largest-contentful-paint has landed (or a fallback fires), so the scene
 *  never competes with the paint the user is actually waiting for during a cold load. */
function afterLcp(): Promise<void> {
  return new Promise(resolve => {
    let done = false
    const finish = () => { if (done) return; done = true; lcpObserver?.disconnect(); if (lcpFallbackTimer) clearTimeout(lcpFallbackTimer); resolve() }
    try {
      lcpObserver = new PerformanceObserver(() => finish())
      lcpObserver.observe({ type: 'largest-contentful-paint', buffered: true })
    } catch {
      finish(); return
    }
    lcpFallbackTimer = setTimeout(finish, LCP_FALLBACK_MS)
  })
}

onMounted(() => {
  const el = host.value
  if (!el || typeof window === 'undefined') return

  const setup = async () => {
    if (cancelled) return
    const THREE = await import('three')
    if (cancelled || !host.value) return

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const coarse = window.matchMedia('(pointer: coarse)').matches
    const starCount = coarse ? 1100 : 2600
    const milkyCount = coarse ? 260 : 620

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: false, powerPreference: 'low-power' })
    // Capped at 1x: a fractional DPR buys little visible sharpness on this field of soft, additive
    // points but scales the raster cost (and GPU power draw) with the square of the ratio — the
    // difference a fanless laptop actually feels.
    renderer.setPixelRatio(1)
    renderer.setSize(el.clientWidth, el.clientHeight)
    renderer.domElement.className = 'hero-galaxy-canvas'
    el.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(52, el.clientWidth / Math.max(1, el.clientHeight), 0.1, 60)
    camera.position.set(0, 0.4, 7.2)

    const group = new THREE.Group()
    scene.add(group)

    // A round, soft sprite: PointsMaterial draws hard squares when it has no map.
    const starSprite = (() => {
      const size = 64
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = size
      const g = canvas.getContext('2d')!
      const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
      grad.addColorStop(0, 'rgba(255,255,255,1)')
      grad.addColorStop(0.22, 'rgba(255,255,255,.92)')
      grad.addColorStop(0.5, 'rgba(255,255,255,.26)')
      grad.addColorStop(1, 'rgba(255,255,255,0)')
      g.fillStyle = grad
      g.fillRect(0, 0, size, size)
      return new THREE.CanvasTexture(canvas)
    })()

    // Survey sphere: tiles on the golden spiral. Starlight, so near-white — mostly a cool
    // white, a handful warm, nothing saturated.
    const positions = new Float32Array(starCount * 3)
    const colors = new Float32Array(starCount * 3)
    const golden = Math.PI * (3 - Math.sqrt(5))
    const coolWhite = new THREE.Color('#cfe0ff')
    const white = new THREE.Color('#ffffff')
    const warmWhite = new THREE.Color('#ffe6c6')
    for (let i = 0; i < starCount; i += 1) {
      const y = 1 - (i / (starCount - 1)) * 2
      const radiusAtY = Math.sqrt(1 - y * y)
      const theta = golden * i
      const r = 3.1 + (Math.sin(i * 12.9898) * 43758.5453 % 1) * 0.15
      positions[i * 3] = Math.cos(theta) * radiusAtY * r
      positions[i * 3 + 1] = y * r
      positions[i * 3 + 2] = Math.sin(theta) * radiusAtY * r
      const h = ((i * 9301 + 49297) % 233280) / 233280
      const color = h > 0.94 ? warmWhite : h > 0.6 ? white : coolWhite
      const dim = 0.42 + h * 0.5
      colors[i * 3] = color.r * dim
      colors[i * 3 + 1] = color.g * dim
      colors[i * 3 + 2] = color.b * dim
    }
    const starGeometry = new THREE.BufferGeometry()
    starGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    starGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    const starMaterial = new THREE.PointsMaterial({ size: 0.075, map: starSprite, vertexColors: true, transparent: true, opacity: 0.95, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true })
    group.add(new THREE.Points(starGeometry, starMaterial))

    // A brighter scatter of foreground stars for depth.
    const nearCount = Math.floor(starCount / 8)
    const nearPositions = new Float32Array(nearCount * 3)
    for (let i = 0; i < nearCount; i += 1) {
      nearPositions[i * 3] = (Math.sin(i * 78.233) * 43758.5453 % 1) * 10 - 5
      nearPositions[i * 3 + 1] = (Math.sin(i * 12.9898) * 24634.6345 % 1) * 6 - 3
      nearPositions[i * 3 + 2] = (Math.sin(i * 39.425) * 11279.8123 % 1) * 3 + 1
    }
    const nearGeometry = new THREE.BufferGeometry()
    nearGeometry.setAttribute('position', new THREE.BufferAttribute(nearPositions, 3))
    const nearMaterial = new THREE.PointsMaterial({ size: 0.05, map: starSprite, color: new THREE.Color('#eef4ff'), transparent: true, opacity: 0.85, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true })
    scene.add(new THREE.Points(nearGeometry, nearMaterial))

    // Faint RA/Dec grid: meridians and parallels on the celestial sphere, merged into one
    // LineSegments draw call.
    const gridRadius = 3.05
    const gridSegments: number[] = []
    const MERIDIAN_STEPS = 40
    const PARALLEL_STEPS = 64
    for (let m = 0; m < 12; m += 1) {
      const ra = (m / 12) * Math.PI * 2
      let prevX = 0, prevY = 0, prevZ = 0, hasPrev = false
      for (let i = 0; i <= MERIDIAN_STEPS; i += 1) {
        const dec = -Math.PI / 2 + (i / MERIDIAN_STEPS) * Math.PI
        const rr = Math.cos(dec) * gridRadius
        const x = Math.cos(ra) * rr, y = Math.sin(dec) * gridRadius, z = Math.sin(ra) * rr
        if (hasPrev) gridSegments.push(prevX, prevY, prevZ, x, y, z)
        prevX = x; prevY = y; prevZ = z; hasPrev = true
      }
    }
    for (let p = 1; p < 6; p += 1) {
      const dec = -Math.PI / 2 + (p / 6) * Math.PI
      const rr = Math.cos(dec) * gridRadius
      const y = Math.sin(dec) * gridRadius
      let prevX = 0, prevZ = 0, hasPrev = false
      for (let i = 0; i <= PARALLEL_STEPS; i += 1) {
        const ra = (i / PARALLEL_STEPS) * Math.PI * 2
        const x = Math.cos(ra) * rr, z = Math.sin(ra) * rr
        if (hasPrev) gridSegments.push(prevX, y, prevZ, x, y, z)
        prevX = x; prevZ = z; hasPrev = true
      }
    }
    const gridGeometry = new THREE.BufferGeometry()
    gridGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(gridSegments), 3))
    const gridMaterial = new THREE.LineBasicMaterial({ color: new THREE.Color('#5b7cff'), transparent: true, opacity: 0.08, depthWrite: false })
    const gridLines = new THREE.LineSegments(gridGeometry, gridMaterial)
    group.add(gridLines)

    // Soft Milky Way haze: a scatter of points biased toward a tilted great-circle band.
    const milkyPositions = new Float32Array(milkyCount * 3)
    const milkyColors = new Float32Array(milkyCount * 3)
    const milkyColor = new THREE.Color('#dce6ff')
    const bandAxis = new THREE.Vector3(0.28, 0.92, 0.27).normalize()
    const arbitrary = Math.abs(bandAxis.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0)
    const bandU = new THREE.Vector3().crossVectors(arbitrary, bandAxis).normalize()
    const bandV = new THREE.Vector3().crossVectors(bandAxis, bandU).normalize()
    const bandRadius = 3.02
    for (let i = 0; i < milkyCount; i += 1) {
      const angle = (i / milkyCount) * Math.PI * 2 + Math.sin(i * 17.31) * 0.02
      const thickness = (((Math.sin(i * 91.7) * 43758.5453) % 1) - 0.5) * 0.46
      const dir = new THREE.Vector3()
        .addScaledVector(bandU, Math.cos(angle))
        .addScaledVector(bandAxis, Math.sin(angle))
        .addScaledVector(bandV, thickness)
        .normalize()
      const r = bandRadius + thickness * 0.05
      milkyPositions[i * 3] = dir.x * r
      milkyPositions[i * 3 + 1] = dir.y * r
      milkyPositions[i * 3 + 2] = dir.z * r
      const density = Math.max(0, 1 - Math.abs(thickness) / 0.23)
      const dim = 0.12 + density * 0.4
      milkyColors[i * 3] = milkyColor.r * dim
      milkyColors[i * 3 + 1] = milkyColor.g * dim
      milkyColors[i * 3 + 2] = milkyColor.b * dim
    }
    const milkyGeometry = new THREE.BufferGeometry()
    milkyGeometry.setAttribute('position', new THREE.BufferAttribute(milkyPositions, 3))
    milkyGeometry.setAttribute('color', new THREE.BufferAttribute(milkyColors, 3))
    const milkyMaterial = new THREE.PointsMaterial({ size: 0.16, map: starSprite, vertexColors: true, transparent: true, opacity: 0.5, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true })
    const milkyWay = new THREE.Points(milkyGeometry, milkyMaterial)
    group.add(milkyWay)

    // Footprint rings: the survey's observing tracks around the sphere.
    const ringMaterial = new THREE.LineBasicMaterial({ color: new THREE.Color('#5b7cff'), transparent: true, opacity: 0.34 })
    const ringMaterialWarm = new THREE.LineBasicMaterial({ color: new THREE.Color('#8fb0ff'), transparent: true, opacity: 0.16 })
    const makeRing = (radius: number, tiltX: number, tiltZ: number, material: InstanceType<typeof THREE.LineBasicMaterial>) => {
      const points: InstanceType<typeof THREE.Vector3>[] = []
      for (let i = 0; i <= 128; i += 1) {
        const angle = (i / 128) * Math.PI * 2
        points.push(new THREE.Vector3(Math.cos(angle) * radius, 0, Math.sin(angle) * radius))
      }
      const ring = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points), material)
      ring.rotation.x = tiltX
      ring.rotation.z = tiltZ
      group.add(ring)
      return ring
    }
    const rings = [
      makeRing(3.5, Math.PI / 2.6, 0.25, ringMaterial),
      makeRing(3.72, Math.PI / 2.1, -0.42, ringMaterialWarm),
      makeRing(3.3, Math.PI / 3.4, 0.9, ringMaterial),
    ]

    group.rotation.z = 0.16
    group.position.x = 1.6

    // Telescope reticle: a circle + crosshair, drawn in camera-facing XY space, that slowly
    // drifts across the visible face of the sphere as if searching the sky.
    const reticleRadius = 0.22
    const reticleSegments: number[] = []
    const RETICLE_CIRCLE_STEPS = 40
    for (let i = 0; i < RETICLE_CIRCLE_STEPS; i += 1) {
      const a0 = (i / RETICLE_CIRCLE_STEPS) * Math.PI * 2
      const a1 = ((i + 1) / RETICLE_CIRCLE_STEPS) * Math.PI * 2
      reticleSegments.push(Math.cos(a0) * reticleRadius, Math.sin(a0) * reticleRadius, 0, Math.cos(a1) * reticleRadius, Math.sin(a1) * reticleRadius, 0)
    }
    const tick = reticleRadius * 0.5
    const edge = reticleRadius * 1.4
    reticleSegments.push(
      -edge, 0, 0, -tick, 0, 0,
      tick, 0, 0, edge, 0, 0,
      0, -edge, 0, 0, -tick, 0,
      0, tick, 0, 0, edge, 0,
    )
    const reticleGeometry = new THREE.BufferGeometry()
    reticleGeometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(reticleSegments), 3))
    const reticleMaterial = new THREE.LineBasicMaterial({ color: new THREE.Color('#9db8ff'), transparent: true, opacity: 0.55, depthWrite: false })
    const reticle = new THREE.LineSegments(reticleGeometry, reticleMaterial)
    scene.add(reticle)

    let pointerX = 0
    let pointerY = 0
    const onPointer = (event: PointerEvent) => {
      pointerX = (event.clientX / window.innerWidth) * 2 - 1
      pointerY = (event.clientY / window.innerHeight) * 2 - 1
    }
    window.addEventListener('pointermove', onPointer, { passive: true })

    let running = !reduced
    let raf = 0
    const clock = new THREE.Clock()
    // Adaptive quality: a weak/integrated GPU shows up as slow frames within the first couple of
    // seconds. Rather than guess a device's class up front, measure it and drop the decorative
    // (non-essential) layers — grid, Milky Way band, footprint rings, reticle — if it's struggling.
    // The main star sphere, the scene's identity, always stays.
    const QUALITY_SAMPLE_MS = 2000
    const QUALITY_FRAME_BUDGET_MS = 20 // ~50fps; above this the canvas is costing more than its share
    let qualityChecked = false
    let qualitySampleStart = 0
    let qualityFrameCount = 0
    let qualityBusyMs = 0
    const downgradeQuality = () => {
      gridLines.visible = false
      milkyWay.visible = false
      for (const ring of rings) ring.visible = false
      reticle.visible = false
    }
    const renderFrame = () => {
      const elapsed = clock.getElapsedTime()
      group.rotation.y = elapsed * 0.05 + pointerX * 0.12
      group.rotation.x = Math.sin(elapsed * 0.11) * 0.05 + pointerY * 0.08
      reticle.position.set(Math.sin(elapsed * 0.13) * 1.8, Math.cos(elapsed * 0.09) * 1.05, 3.4)
      reticle.rotation.z = elapsed * 0.05
      const t0 = !qualityChecked ? performance.now() : 0
      renderer.render(scene, camera)
      if (!qualityChecked) {
        const now = performance.now()
        if (qualitySampleStart === 0) qualitySampleStart = now
        qualityFrameCount += 1
        qualityBusyMs += now - t0
        if (now - qualitySampleStart >= QUALITY_SAMPLE_MS) {
          qualityChecked = true
          if (qualityBusyMs / Math.max(1, qualityFrameCount) > QUALITY_FRAME_BUDGET_MS) downgradeQuality()
        }
      }
    }
    const loop = () => {
      if (!running) return
      renderFrame()
      raf = requestAnimationFrame(loop)
    }

    const visibility = new IntersectionObserver(entries => {
      const visible = entries.some(entry => entry.isIntersecting)
      if (reduced) return
      if (visible && !running) { running = true; loop() }
      if (!visible) { running = false; cancelAnimationFrame(raf) }
    }, { threshold: 0.05 })
    visibility.observe(el)
    const onHidden = () => {
      if (reduced) return
      if (document.hidden) { running = false; cancelAnimationFrame(raf) }
      else if (!running) { running = true; loop() }
    }
    document.addEventListener('visibilitychange', onHidden)

    const resize = new ResizeObserver(() => {
      renderer.setSize(el.clientWidth, el.clientHeight)
      camera.aspect = el.clientWidth / Math.max(1, el.clientHeight)
      camera.updateProjectionMatrix()
      if (reduced) renderFrame()
    })
    resize.observe(el)

    renderFrame()
    if (running) loop()

    dispose = () => {
      running = false
      cancelAnimationFrame(raf)
      visibility.disconnect()
      resize.disconnect()
      document.removeEventListener('visibilitychange', onHidden)
      window.removeEventListener('pointermove', onPointer)
      starGeometry.dispose(); starMaterial.dispose(); starSprite.dispose()
      nearGeometry.dispose(); nearMaterial.dispose()
      gridGeometry.dispose(); gridMaterial.dispose()
      milkyGeometry.dispose(); milkyMaterial.dispose()
      ringMaterial.dispose(); ringMaterialWarm.dispose()
      reticleGeometry.dispose(); reticleMaterial.dispose()
      renderer.dispose()
      renderer.domElement.remove()
    }
  }

  const w = window as unknown as {
    requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
    cancelIdleCallback?: (handle: number) => void
  }
  void afterLcp().then(() => {
    if (cancelled) return
    if (typeof w.requestIdleCallback === 'function') {
      idleHandle = w.requestIdleCallback(() => { void setup() }, { timeout: 1500 })
    } else {
      idleTimer = setTimeout(() => { void setup() }, 200)
    }
  })
})

onBeforeUnmount(() => {
  cancelled = true
  const w = window as unknown as { cancelIdleCallback?: (handle: number) => void }
  if (idleHandle !== null && typeof w.cancelIdleCallback === 'function') w.cancelIdleCallback(idleHandle)
  if (idleTimer !== null) clearTimeout(idleTimer)
  lcpObserver?.disconnect()
  if (lcpFallbackTimer !== null) clearTimeout(lcpFallbackTimer)
  dispose?.()
})
</script>

<template>
  <div ref="host" class="hero-galaxy" aria-hidden="true"></div>
</template>

<style scoped>
.hero-galaxy { position: absolute; inset: 0; z-index: 1; pointer-events: none; }
.hero-galaxy :deep(.hero-galaxy-canvas) { width: 100%; height: 100%; mix-blend-mode: screen; opacity: .92; }
</style>
