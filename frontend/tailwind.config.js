/**
 * Tailwind is mapped onto the design tokens defined in `src/tokens.css`.
 *
 * Components reference these utilities (e.g. `bg-surface`, `text-text-muted`,
 * `border-st-review-fg`) instead of raw palette colors, so a single edit in
 * `tokens.css` re-themes the whole app and no hex value ever lives inside a
 * component.
 *
 * Opacity modifiers (`bg-accent/40`, `border-st-error-fg/40`) only work when
 * Tailwind can inject an alpha into the colour. A bare `var(--token)` hides the
 * colour from Tailwind, so the modifier is silently dropped. Every colour is
 * therefore wrapped in `color(--token)`: a `color-mix()` over the hex token that
 * carries Tailwind's `<alpha-value>` placeholder. The hex tokens in `tokens.css`
 * stay the single source of truth.
 */

/** Alpha-aware colour over a CSS custom property. */
const color = (token) =>
  `color-mix(in srgb, var(--${token}) calc(<alpha-value> * 100%), transparent)`

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 3.1 Neutrals and accent
        bg: color('bg'),
        surface: color('surface'),
        'surface-muted': color('surface-muted'),
        border: color('border'),
        text: color('text'),
        'text-muted': color('text-muted'),
        accent: color('accent'),
        'accent-soft': color('accent-soft'),
        'on-accent': color('on-accent'),

        // 3.2 Status colors: `<status>-fg` for text/icon, `<status>-bg` for badges
        'st-neutral-fg': color('st-neutral-fg'),
        'st-neutral-bg': color('st-neutral-bg'),
        'st-progress-fg': color('st-progress-fg'),
        'st-progress-bg': color('st-progress-bg'),
        'st-review-fg': color('st-review-fg'),
        'st-review-bg': color('st-review-bg'),
        'st-success-fg': color('st-success-fg'),
        'st-success-bg': color('st-success-bg'),
        'st-error-fg': color('st-error-fg'),
        'st-error-bg': color('st-error-bg'),
        'st-warn-fg': color('st-warn-fg'),
        'st-warn-bg': color('st-warn-bg'),
      },

      fontFamily: {
        // Section 4: UI chrome, notes reading pane, code — never mixed.
        sans: 'var(--font-ui)',
        ui: 'var(--font-ui)',
        notes: 'var(--font-notes)',
        mono: 'var(--font-mono)',
      },

      fontSize: {
        page: [
          'var(--size-page-title)',
          { lineHeight: 'var(--leading-page-title)', fontWeight: '600' },
        ],
        section: [
          'var(--size-section-title)',
          { lineHeight: 'var(--leading-section-title)', fontWeight: '600' },
        ],
        body: ['var(--size-body)', { lineHeight: 'var(--leading-body)', fontWeight: '400' }],
        label: ['var(--size-label)', { lineHeight: 'var(--leading-label)', fontWeight: '500' }],
        notes: ['var(--size-notes)', { lineHeight: 'var(--leading-notes)', fontWeight: '400' }],
        code: ['var(--size-code)', { lineHeight: 'var(--leading-code)' }],
      },

      // `leading-notes` (26px) for the Review reading pane.
      lineHeight: {
        notes: 'var(--leading-notes)',
      },

      // Section 5: 4px base scale, use only 4/8/12/16/24/32.
      spacing: {
        1: 'var(--space-1)',
        2: 'var(--space-2)',
        3: 'var(--space-3)',
        4: 'var(--space-4)',
        5: 'var(--space-5)',
        6: 'var(--space-6)',
      },

      // Section 7: 16px icons in tables, 20px elsewhere. (`size-5` would be 24px
      // because the spacing scale above maps 5 to 24px.)
      size: {
        icon: 'var(--icon-size)',
        'icon-lg': 'var(--icon-size-lg)',
      },

      maxWidth: {
        page: 'var(--page-max-width)',
        modal: 'var(--modal-width)',
      },

      borderRadius: {
        // Section 2: 6px for controls and cards, 10px for modals.
        DEFAULT: 'var(--radius)',
        modal: 'var(--radius-modal)',
        pill: '9999px',
      },

      height: {
        topbar: 'var(--topbar-height)',
        control: 'var(--control-height)',
        row: 'var(--row-height)',
        bar: 'var(--progress-bar-height)',
      },

      minHeight: {
        row: 'var(--row-height)',
        control: 'var(--control-height)',
      },

      boxShadow: {
        // Section 2: almost no shadows — popovers and modals only.
        popover: '0 8px 24px rgb(15 23 42 / 0.12)',
        modal: '0 16px 48px rgb(15 23 42 / 0.18)',
      },

      transitionDuration: {
        DEFAULT: 'var(--transition-duration)',
      },
    },
  },
  plugins: [],
}
