/* ESLint 8 (eslintrc) config -- matches the `--ext ts,tsx` lint script and the
 * @typescript-eslint 7 / react-hooks / react-refresh packages already in
 * devDependencies. Flat config would ignore --ext, so keep this format unless
 * the script changes too.
 *
 * Tuned so `npm run lint` passes on the tree as it stands. A config that fails
 * with hundreds of pre-existing problems gets ignored, which is worse than
 * having none -- so the two categories the codebase is not ready for are
 * turned down deliberately, and everything else gates.
 */
module.exports = {
  root: true,
  env: { browser: true, es2020: true, node: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', 'coverage', 'node_modules', '*.cjs'],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['react-refresh'],
  rules: {
    // 188 hits today, mostly API response shapes and `catch (error: any)`.
    // Turning these into real types is worthwhile but it is its own piece of
    // work; flip this to 'warn' when starting it.
    '@typescript-eslint/no-explicit-any': 'off',

    // Fires on modules exporting both components and helpers. Cosmetic, and
    // only affects hot-reload granularity in dev.
    'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

    // 14 hits. Each needs judgement -- adding a missing dep can change when an
    // effect re-runs -- so these inform rather than block.
    'react-hooks/exhaustive-deps': 'warn',
  },
};
