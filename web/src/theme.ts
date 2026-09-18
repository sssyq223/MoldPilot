export type ColorTheme = 'light' | 'dark'

const STORAGE_KEY = 'agent-workbench.theme'

export function storedTheme(): ColorTheme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function applyTheme(theme: ColorTheme) {
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Storage can be unavailable in hardened browser contexts; the live choice still applies.
  }
}

applyTheme(storedTheme())
