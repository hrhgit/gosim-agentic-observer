// Astronomy easter eggs: the moon-phase favicon, the console greeting for whoever opens
// devtools, and the meteor shower. Everything here is decorative — every failure is swallowed.

const MOON = ['🌑', '🌒', '🌓', '🌔', '🌕', '🌖', '🌗', '🌘']

/** Moon phase in [0, 1): 0 = new moon, 0.5 = full moon. Good to a few hours. */
export function moonPhase(date = new Date()): number {
  const synodic = 29.530588853
  const days = (date.getTime() - Date.UTC(2000, 0, 6, 18, 14)) / 86_400_000
  return ((days % synodic) + synodic) % synodic / synodic
}

/** True on the day of a full moon (about ±12 hours around the exact moment). */
export function isFullMoonToday(): boolean {
  try {
    if (new URLSearchParams(window.location.search).get('moon') === 'full') return true
  } catch { /* no window */ }
  return Math.abs(moonPhase() - 0.5) < 0.017
}

/** Mid-Autumn Festival (the 15th day of the 8th lunar month), as calendar dates in China. */
const MID_AUTUMN = ['2026-09-25', '2027-09-15', '2028-10-03', '2029-09-22', '2030-09-12']

function isoDate(date: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}

/** Days the greeting stays up: the festival day and the two days after it. */
const MID_AUTUMN_DAYS = 3

/** True from the festival day through two days later, in China or in the visitor's own time zone; `?egg=midautumn` previews it. */
export function isMidAutumnToday(date = new Date()): boolean {
  try {
    if (new URLSearchParams(window.location.search).get('egg') === 'midautumn') return true
    const days = new Set(MID_AUTUMN.flatMap(day => Array.from({ length: MID_AUTUMN_DAYS }, (_, i) =>
      new Date(Date.parse(day + 'T00:00:00Z') + i * 86_400_000).toISOString().slice(0, 10))))
    return days.has(isoDate(date, 'Asia/Shanghai')) || days.has(isoDate(date))
  } catch {
    return false
  }
}

/** The tab icon follows tonight's real moon. */
export function installMoonFavicon() {
  try {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 64
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.font = '56px serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(MOON[Math.round(moonPhase() * 8) % 8]!, 32, 36)
    const url = canvas.toDataURL('image/png')
    document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"]').forEach(l => l.remove())
    const link = document.createElement('link')
    link.rel = 'icon'
    link.type = 'image/png'
    link.href = url
    document.head.appendChild(link)
  } catch { /* the static favicon stays */ }
}

/** A hello for whoever opens the developer tools. */
export function consoleGreeting() {
  try {
    const days = Math.floor((Date.now() - Date.UTC(1977, 8, 5)) / 86_400_000)
    console.log(
      `%c
   ✦        ·           ✦
        _____
       /  ◉  \\     AGENTIC OBSERVER HACKATHON · 智能体巡天黑客松
      |_______|
       /     \\          ·
  ·                承 900 秒一次的凝视
`,
      'color:#78a6ff; font-family:monospace',
    )
    console.log(`旅行者 1 号已经飞了 ${days.toLocaleString()} 天，仍在向宇宙深处报数。`)
    console.log(`Voyager 1 has been flying for ${days.toLocaleString()} days, still calling home.`)
    console.log('想教望远镜自己思考？报名 → https://create.gosim.org/survey26/platform/register')
  } catch { /* consoles vary */ }
}

let showerBusy = false
/** A brief meteor shower across the viewport, plus a one-line toast. */
export function meteorShower(message: string) {
  if (showerBusy) return
  showerBusy = true
  const toast = document.createElement('div')
  toast.className = 'egg-comet-toast'
  toast.textContent = message
  document.body.appendChild(toast)
  window.setTimeout(() => toast.remove(), 4200)
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    window.setTimeout(() => { showerBusy = false }, 5000)
    return
  }
  const canvas = document.createElement('canvas')
  canvas.className = 'egg-meteor-canvas'
  canvas.width = window.innerWidth
  canvas.height = window.innerHeight
  document.body.appendChild(canvas)
  const ctx = canvas.getContext('2d')
  if (!ctx) { canvas.remove(); showerBusy = false; return }
  type Meteor = { x: number; y: number; v: number; len: number; delay: number }
  const meteors: Meteor[] = Array.from({ length: 26 }, () => ({
    x: Math.random() * canvas.width * 1.3,
    y: -40 - Math.random() * canvas.height * 0.4,
    v: 9 + Math.random() * 7,
    len: 90 + Math.random() * 130,
    delay: Math.random() * 26,
  }))
  let frame = 0
  const step = () => {
    frame += 1
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    let alive = false
    for (const m of meteors) {
      if (frame < m.delay) { alive = true; continue }
      m.x -= m.v * 0.62
      m.y += m.v
      if (m.y > canvas.height + m.len) continue
      alive = true
      const g = ctx.createLinearGradient(m.x, m.y, m.x + m.len * 0.62, m.y - m.len)
      g.addColorStop(0, 'rgba(255,255,255,.95)')
      g.addColorStop(1, 'rgba(120,166,255,0)')
      ctx.strokeStyle = g
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(m.x, m.y)
      ctx.lineTo(m.x + m.len * 0.62, m.y - m.len)
      ctx.stroke()
    }
    if (alive && frame < 240) requestAnimationFrame(step)
    else { canvas.remove(); showerBusy = false }
  }
  requestAnimationFrame(step)
}
