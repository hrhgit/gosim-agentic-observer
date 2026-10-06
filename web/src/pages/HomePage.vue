<script setup lang="ts">
import WechatGroup from '../components/WechatGroup.vue'
import { useScrollReveal } from '../composables/useScrollReveal'
import HeroSection from '../components/sections/HeroSection.vue'
import SectionRail from '../components/layout/SectionRail.vue'
import FilmSection from '../components/sections/FilmSection.vue'
import QuestSection from '../components/sections/QuestSection.vue'
import ParticipantsSection from '../components/sections/ParticipantsSection.vue'
import SimpleVisionSection from '../components/sections/SimpleVisionSection.vue'
import SimpleMissionSection from '../components/sections/SimpleMissionSection.vue'
import SimpleParticipateSection from '../components/sections/SimpleParticipateSection.vue'
import PrizeSection from '../components/sections/PrizeSection.vue'
import EvaluationSection from '../components/sections/EvaluationSection.vue'
import SubmissionSection from '../components/sections/SubmissionSection.vue'
import LeaderboardSection from '../components/sections/LeaderboardSection.vue'
import OrganizersSection from '../components/sections/OrganizersSection.vue'
import CtaSection from '../components/sections/CtaSection.vue'

useScrollReveal()

const railSections = [
  { id: 'top', key: 'home.rail.top' },
  { id: 'film', key: 'home.rail.film' },
  { id: 'quest', key: 'home.rail.quest' },
  { id: 'board', key: 'home.rail.board' },
  { id: 'participants', key: 'home.rail.participants' },
  { id: 'vision', key: 'home.rail.vision' },
  { id: 'mission', key: 'home.rail.mission' },
  { id: 'participate', key: 'home.rail.participate' },
  { id: 'prizes', key: 'home.rail.prizes' },
  { id: 'evaluation', key: 'home.rail.evaluation' },
  { id: 'submission', key: 'home.rail.submission' },
  { id: 'organizers', key: 'home.rail.organizers' },
  { id: 'cta', key: 'home.rail.cta' },
]
</script>

<template>
  <main>
    <SectionRail :sections="railSections" />
    <HeroSection />
    <FilmSection class="section-tint-b" />
    <QuestSection />
    <LeaderboardSection id="board" class="section-tint-b" />
    <ParticipantsSection />
    <section class="section"><div class="wrap"><WechatGroup /></div></section>
    <SimpleVisionSection id="vision" class="section-tint-b" />
    <SimpleMissionSection id="mission" />
    <SimpleParticipateSection id="participate" class="section-tint-b" />
    <PrizeSection id="prizes" />
    <EvaluationSection id="evaluation" class="section-tint-b" />
    <SubmissionSection id="submission" />
    <OrganizersSection />
    <CtaSection id="cta" />
  </main>
</template>
