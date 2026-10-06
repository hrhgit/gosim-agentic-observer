import { computed, ref } from 'vue'
import { competition } from '../stores/competition'
import { useAuth } from '../stores/auth'
import { browserStorage, readQuestFlags, rememberQuestFlag, type QuestFlag, type QuestMode } from '../lib/quest'

// Bumped on every remembered step so each open quest panel re-reads storage.
const version = ref(0)

/** Quest steps the database cannot see (kit download, replay visit), remembered per user and mode. */
export function useQuestFlags() {
  const { me } = useAuth()
  const flags = computed(() => {
    void version.value
    return me.value ? readQuestFlags(browserStorage(), me.value.id, competition.mode) : {}
  })
  function remember(flag: QuestFlag, mode: QuestMode = competition.mode) {
    if (!me.value) return
    rememberQuestFlag(browserStorage(), me.value.id, mode, flag)
    version.value++
  }
  return { flags, remember }
}
