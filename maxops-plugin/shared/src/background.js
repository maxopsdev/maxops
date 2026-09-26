const extensionApi = globalThis.maxopsBrowser || chrome;

const DEFAULT_SETTINGS = {
  backendBaseUrl: "http://localhost:8000",
  frontendBaseUrl: "http://localhost:3000",
};

const normalizeBaseUrl = (value) => {
  const fallback = DEFAULT_SETTINGS.backendBaseUrl;
  const raw = String(value || fallback).trim().replace(/\/+$/, "");

  try {
    const parsed = new URL(raw);
    const isLocal =
      parsed.protocol === "http:" &&
      (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1");

    return isLocal ? parsed.origin : fallback;
  } catch (_error) {
    return fallback;
  }
};

const storageGet = (defaults) =>
  new Promise((resolve) => {
    extensionApi.storage.sync.get(defaults, resolve);
  });

const storageSet = (value) =>
  new Promise((resolve) => {
    extensionApi.storage.sync.set(value, resolve);
  });

const getSettings = async () => {
  const stored = await storageGet(DEFAULT_SETTINGS);
  return {
    backendBaseUrl: normalizeBaseUrl(stored.backendBaseUrl),
    frontendBaseUrl: normalizeBaseUrl(stored.frontendBaseUrl),
  };
};

const fetchJson = async (path) => {
  const settings = await getSettings();
  const response = await fetch(`${settings.backendBaseUrl}${path}`, {
    headers: {
      Accept: "application/json",
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`MaxOps backend returned ${response.status}`);
  }

  return response.json();
};

extensionApi.runtime.onInstalled.addListener(async () => {
  const existing = await storageGet(DEFAULT_SETTINGS);
  await storageSet({
    backendBaseUrl: normalizeBaseUrl(existing.backendBaseUrl),
  });
});

const OVERVIEW_ENDPOINTS = {
  MAXOPS_GET_EC2_OVERVIEW: "/api/v1/inventory/ec2/overview",
  MAXOPS_GET_DYNAMODB_OVERVIEW: "/api/v1/inventory/dynamodb/overview",
  MAXOPS_GET_EBS_OVERVIEW: "/api/v1/inventory/ebs/overview",
  MAXOPS_GET_RDS_OVERVIEW: "/api/v1/inventory/rds/overview",
  MAXOPS_GET_ELASTICACHE_OVERVIEW: "/api/v1/inventory/elasticache/overview",
};

extensionApi.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const endpoint = OVERVIEW_ENDPOINTS[message?.type];
  if (!endpoint) {
    return false;
  }

  fetchJson(endpoint)
    .then((payload) => sendResponse({ ok: true, payload }))
    .catch((error) => sendResponse({ ok: false, error: error.message }));

  return true;
});
