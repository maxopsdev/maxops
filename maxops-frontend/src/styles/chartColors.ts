/**
 * Shared chart color tokens for the navy/teal/gold/rose brand palette.
 *
 * Recharts (and other SVG-based chart libraries) need literal color values as
 * props (`fill`, `stroke`, `stopColor`, etc.) — Tailwind classes don't apply
 * to SVG attributes. This module centralizes those literal values so pages
 * don't hardcode hex strings or redefine local `CHART_COLORS` arrays.
 *
 * Keep these in sync with the brand scales in `tailwind.config.js`
 * (`gray`, `primary`, `success`, `warning`, `danger`).
 */

/**
 * Categorical palette for multi-series charts (pie/bar/line legends, etc.).
 * Ordered so the first colors are used most often — lead with brand teal and
 * gold, then rose, then two additional harmonized-but-distinguishable hues
 * (a muted slate-blue and a soft violet) that read well on both a light and
 * a near-black navy chart background.
 */
export const CHART_COLORS = [
  '#34e0c4', // teal-400 (primary) — lead brand color
  '#f5a623', // gold-500 (warning) — brand gold
  '#ef4460', // rose-500 (danger) — brand rose
  '#0d9d89', // teal-600 (primary, deeper) — secondary teal for contrast
  '#5b7ba8', // muted slate-blue — harmonized neutral accent
  '#9b7fd4', // soft violet — harmonized categorical accent
] as const;

/**
 * Semantic status colors for per-resource-state maps (e.g. instance state,
 * check severity). Built from the brand success/danger/warning scales so
 * pages import these instead of hardcoding `#16a34a` / `#dc2626` etc.
 */
export const STATUS_COLORS = {
  // Running / healthy / available / attached
  running: '#22c55e', // success-500
  healthy: '#22c55e', // success-500
  // Stopped / error / unhealthy / detached / failed
  stopped: '#ef4460', // danger-500
  error: '#ef4460', // danger-500
  unhealthy: '#ef4460', // danger-500
  // Actionable / warning / pending / degraded / idle-but-attention-needed
  actionable: '#f5a623', // warning-500
  warning: '#f5a623', // warning-500
  pending: '#f5a623', // warning-500
  // Idle / stopping / starting / transitional / unknown
  idle: '#7e8db3', // gray-400
  transitional: '#7e8db3', // gray-400
  unknown: '#7e8db3', // gray-400
} as const;

export type StatusColorKey = keyof typeof STATUS_COLORS;

/**
 * Dark-mode-aware axis/grid/tick colors for chart chrome (CartesianGrid,
 * XAxis/YAxis tick + line, reference lines, etc.). Pair with `useTheme()`
 * from `src/contexts/ThemeContext.tsx` to pick the right value:
 *
 *   const { theme } = useTheme();
 *   <CartesianGrid stroke={CHART_GRID_COLOR[theme]} />
 *   <XAxis tick={{ fill: CHART_TICK_COLOR[theme] }} />
 */
export const CHART_GRID_COLOR = {
  light: '#d3daeb', // gray-200
  dark: '#414d72', // gray-600
} as const;

export const CHART_TICK_COLOR = {
  light: '#59678f', // gray-500
  dark: '#aab6d1', // gray-300
} as const;

export const CHART_AXIS_LINE_COLOR = {
  light: '#aab6d1', // gray-300
  dark: '#414d72', // gray-600
} as const;

/**
 * Small helper for components that don't want to import `useTheme` directly
 * (e.g. plain chart-config modules) — pass the current theme string through.
 */
export function chartGridColor(theme: 'light' | 'dark'): string {
  return CHART_GRID_COLOR[theme];
}

export function chartTickColor(theme: 'light' | 'dark'): string {
  return CHART_TICK_COLOR[theme];
}

export function chartAxisLineColor(theme: 'light' | 'dark'): string {
  return CHART_AXIS_LINE_COLOR[theme];
}
