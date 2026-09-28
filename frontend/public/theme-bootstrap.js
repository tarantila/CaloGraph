(function () {
  var preference = 'system'
  try {
    var stored = window.localStorage.getItem('calograph.theme')
    if (stored === 'light' || stored === 'dark') preference = stored
  } catch {
    // Storage may be unavailable in private or restricted browsing contexts.
  }

  var effective = preference
  if (preference === 'system') {
    var osDark = true
    try {
      osDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    } catch {
      // Match the CSS dark default when system preference cannot be queried.
    }
    effective = osDark ? 'dark' : 'light'
  }

  document.documentElement.dataset.themePreference = preference
  document.documentElement.dataset.theme = effective
  var colorScheme = document.querySelector('meta[name="color-scheme"]')
  if (colorScheme) colorScheme.content = effective
  document.querySelectorAll('meta[name="theme-color"]').forEach(function (meta) {
    meta.content = effective === 'dark' ? '#081321' : '#f6f7fb'
  })
}())
