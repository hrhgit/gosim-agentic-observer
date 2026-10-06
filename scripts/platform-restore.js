// Run before Vue and Supabase so deep links retain their query and auth fragment.
(function () {
  try {
    var destination = sessionStorage.getItem('gosim-platform-redirect')
    sessionStorage.removeItem('gosim-platform-redirect')
    if (destination && destination.indexOf('/survey26/platform/') === 0) {
      var url = new URL(destination, location.origin)
      if (url.origin === location.origin && url.pathname.indexOf('/survey26/platform/') === 0) {
        sessionStorage.removeItem('gosim-pages-redirect')
        history.replaceState(null, '', url.pathname + url.search + url.hash)
      }
    }
  } catch (_) {}
})()
