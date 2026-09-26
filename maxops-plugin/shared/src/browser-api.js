(() => {
  const runtime = globalThis.chrome || globalThis.browser;

  if (!runtime) {
    throw new Error("MaxOps requires a WebExtensions-compatible browser API.");
  }

  globalThis.maxopsBrowser = runtime;
})();
