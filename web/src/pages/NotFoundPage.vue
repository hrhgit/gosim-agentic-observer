<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../composables/useI18n'

const { t, pick } = useI18n()

// A wrong turn lands on an unobserved tile; introduce a real object instead of a dead end.
const OBJECTS = [
  { m: 'M31', zh: '仙女座星系', en: 'Andromeda Galaxy', zhLine: '离银河系最近的大星系，250 万光年外，暗夜里肉眼可见。', enLine: 'The nearest big galaxy to our own — 2.5 million light-years away, visible to the naked eye on a dark night.', hue: 220 },
  { m: 'M42', zh: '猎户座大星云', en: 'Orion Nebula', zhLine: '一间正在生产恒星的育婴房，冬夜猎户腰带下最亮的那团光。', enLine: 'A nursery where stars are being born — the bright glow below Orion’s belt on winter nights.', hue: 320 },
  { m: 'M45', zh: '昴星团', en: 'The Pleiades', zhLine: '七姐妹星团：一小撮年轻的蓝色恒星，几乎每个文明都给它起过名字。', enLine: 'The Seven Sisters: a huddle of young blue stars nearly every culture has named.', hue: 200 },
  { m: 'M13', zh: '武仙座球状星团', en: 'Hercules Cluster', zhLine: '几十万颗恒星挤成一团蜂群，年纪比地球大两倍还多。', enLine: 'Hundreds of thousands of stars packed into one swarm, more than twice as old as the Earth.', hue: 45 },
  { m: 'M51', zh: '涡状星系', en: 'Whirlpool Galaxy', zhLine: '一对正在相拥的星系，引力把旋臂拉成了教科书级的漩涡。', enLine: 'Two galaxies mid-embrace, gravity pulling their arms into a textbook spiral.', hue: 260 },
  { m: 'M104', zh: '草帽星系', en: 'Sombrero Galaxy', zhLine: '明亮的核心戴着一圈厚厚的尘埃帽檐，侧着看真像一顶草帽。', enLine: 'A bright core under a thick brim of dust — seen edge-on, it really is a hat.', hue: 30 },
  { m: 'M1', zh: '蟹状星云', en: 'Crab Nebula', zhLine: '一颗恒星在公元 1054 年爆炸的残骸——中国宋代的天文学家记下了那次爆发。', enLine: 'The wreck of a star that exploded in the year 1054 — astronomers of Song-dynasty China wrote the blast down.', hue: 0 },
  { m: 'M81', zh: '波德星系', en: 'Bode’s Galaxy', zhLine: '北天最上镜的旋涡星系之一，一台小望远镜就能找到它。', enLine: 'One of the most photogenic spirals in the northern sky — findable with a small telescope.', hue: 190 },
]
const obj = OBJECTS[Math.floor(Math.random() * OBJECTS.length)]!
const objName = computed(() => pick(obj.en, obj.zh))
const objLine = computed(() => pick(obj.enLine, obj.zhLine))
</script>

<template>
  <main class="poster-canvas min-h-[70vh]">
    <section class="section"><div class="wrap-narrow">
      <span class="poster-kicker">404 · {{ pick('UNCHARTED TILE', '未观测天区') }}</span>
      <h1 class="section-title mt-6">{{ pick('This tile has not been observed yet', '这片天区还没被观测过') }}</h1>
      <p class="lede mt-6">{{ pick('The page you were looking for is not in the survey catalogue. Here is a real one from it:', '你要找的页面不在巡天目录里。下面是目录里一个真实的天体：') }}</p>

      <div class="egg-object-card mt-10" :style="`--obj-hue:${obj.hue}`">
        <i class="egg-object-disc" aria-hidden="true"></i>
        <div>
          <span class="egg-object-m">{{ obj.m }}</span>
          <h2>{{ objName }}</h2>
          <p>{{ objLine }}</p>
        </div>
      </div>

      <p class="mt-10 flex flex-wrap gap-3">
        <router-link class="btn primary" to="/">{{ t('common.home') }} →</router-link>
        <router-link class="btn" to="/register">{{ t('nav.register') }} →</router-link>
      </p>
    </div></section>
  </main>
</template>
