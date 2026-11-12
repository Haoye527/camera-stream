(function () {
  'use strict';

  const storageKey = 'easynvr-color-theme';
  const defaultTheme = 'dark';
  const root = document.documentElement;
  const validTheme = value => value === 'dark' || value === 'light';

  function applyTheme(theme) {
    root.dataset.theme = validTheme(theme) ? theme : defaultTheme;
    for (const button of document.querySelectorAll('[data-theme-toggle]')) {
      button.setAttribute('aria-label', root.dataset.theme === 'dark' ? '切换到浅色主题' : '切换到深色主题');
    }
  }

  try {
    const stored = localStorage.getItem(storageKey);
    applyTheme(validTheme(stored) ? stored : defaultTheme);
  } catch {
    applyTheme(defaultTheme);
  }

  document.addEventListener('DOMContentLoaded', () => {
    applyTheme(root.dataset.theme);
    for (const button of document.querySelectorAll('[data-theme-toggle]')) {
      button.addEventListener('click', () => {
        const theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
        applyTheme(theme);
        try { localStorage.setItem(storageKey, theme); } catch { /* Keep the choice for this page. */ }
      });
    }
  });

  window.addEventListener('storage', event => {
    if (event.key === storageKey || event.key === null) applyTheme(event.newValue);
  });
})();
