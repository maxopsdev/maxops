const extensionApi = globalThis.maxopsBrowser || chrome;
const DEFAULT_BACKEND_URL = "http://localhost:8000";
const DEFAULT_FRONTEND_URL = "http://localhost:3000";

const normalizeBaseUrl = (value) => {
  const raw = String(value || DEFAULT_BACKEND_URL).trim().replace(/\/+$/, "");
  const parsed = new URL(raw);
  const isLocal =
    parsed.protocol === "http:" &&
    (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1");

  if (!isLocal) {
    throw new Error("Use a local HTTP backend URL.");
  }

  return parsed.origin;
};

const input = document.getElementById("backendBaseUrl");
const frontendInput = document.getElementById("frontendBaseUrl");
const status = document.getElementById("status");

extensionApi.storage.sync.get(
  {
    backendBaseUrl: DEFAULT_BACKEND_URL,
    frontendBaseUrl: DEFAULT_FRONTEND_URL,
  },
  (settings) => {
  input.value = settings.backendBaseUrl;
    frontendInput.value = settings.frontendBaseUrl;
  },
);

document.getElementById("settingsForm").addEventListener("submit", (event) => {
  event.preventDefault();

  try {
    const backendBaseUrl = normalizeBaseUrl(input.value);
    const frontendBaseUrl = normalizeBaseUrl(frontendInput.value || DEFAULT_FRONTEND_URL);
    extensionApi.storage.sync.set({ backendBaseUrl, frontendBaseUrl }, () => {
      input.value = backendBaseUrl;
      frontendInput.value = frontendBaseUrl;
      status.textContent = "Saved.";
      status.className = "status status--ok";
    });
  } catch (error) {
    status.textContent = error.message;
    status.className = "status status--error";
  }
});
