const extensionApi = globalThis.maxopsBrowser || chrome;
const INSTANCE_ID_PATTERN = /\bi-[0-9a-f]{8,17}\b/g;
const EBS_VOLUME_ID_PATTERN = /\bvol-[0-9a-f]{8,17}\b/;
const OVERLAY_ATTR = "data-maxops-overlay";
const SOURCE_ATTR = "data-maxops-source";
const DDB_HEADER_ATTR = "data-maxops-ddb-header";
const DDB_CELL_ATTR = "data-maxops-ddb-cell";
const DDB_CONTENT_ATTR = "data-maxops-ddb-content";
const DDB_COLUMN_ATTR = "data-maxops-ddb-column";
const EBS_HEADER_ATTR = "data-maxops-ebs-header";
const EBS_CELL_ATTR = "data-maxops-ebs-cell";
const EBS_CONTENT_ATTR = "data-maxops-ebs-content";
const EBS_COLUMN_ATTR = "data-maxops-ebs-column";
const EBS_INLINE_ATTR = "data-maxops-ebs-inline";
const EC2_HEADER_ATTR = "data-maxops-ec2-header";
const EC2_CELL_ATTR = "data-maxops-ec2-cell";
const EC2_CONTENT_ATTR = "data-maxops-ec2-content";
const EC2_COLUMN_ATTR = "data-maxops-ec2-column";
const RDS_HEADER_ATTR = "data-maxops-rds-header";
const RDS_CELL_ATTR = "data-maxops-rds-cell";
const RDS_CONTENT_ATTR = "data-maxops-rds-content";
const RDS_COLUMN_ATTR = "data-maxops-rds-column";
const ELASTICACHE_HEADER_ATTR = "data-maxops-elasticache-header";
const ELASTICACHE_CELL_ATTR = "data-maxops-elasticache-cell";
const ELASTICACHE_CONTENT_ATTR = "data-maxops-elasticache-content";
const ELASTICACHE_COLUMN_ATTR = "data-maxops-elasticache-column";
const DEFAULT_FRONTEND_URL = "http://localhost:3000";
const REFRESH_INTERVAL_MS = 60_000;
const RESCAN_DELAY_MS = 500;
const DDB_COLUMNS = [
  { key: "price", label: "MaxOps price", width: "140px" },
  { key: "savings", label: "MaxOps savings", width: "160px" },
  { key: "findings", label: "MaxOps findings", width: "180px" },
];
const EBS_COLUMNS = DDB_COLUMNS;
const EC2_COLUMNS = DDB_COLUMNS;
const RDS_COLUMNS = DDB_COLUMNS;
const ELASTICACHE_COLUMNS = DDB_COLUMNS;

let cachedEc2Overview = null;
let cachedInstances = new Map();
let cachedDynamoDbOverview = null;
let cachedDynamoDbTables = new Map();
let cachedEbsOverview = null;
let cachedEbsVolumes = new Map();
let cachedRdsOverview = null;
let cachedRdsInstances = new Map();
let cachedElasticacheOverview = null;
let cachedElasticacheResources = new Map();
let maxOpsUiBaseUrl = DEFAULT_FRONTEND_URL;
let refreshTimer = null;
let scanTimer = null;
let isApplyingMaxOpsDom = false;
let extensionContextActive = true;

const formatCurrency = (value) => {
  const amount = Number(value || 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: amount >= 100 ? 0 : 2,
  }).format(amount);
};

const normalizeText = (value) => String(value || "").trim();

const toNumber = (value) => {
  const amount = Number(value);
  return Number.isFinite(amount) ? amount : 0;
};

const normalizeLocalBaseUrl = (value, fallback) => {
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

const getRuntimeError = () => {
  try {
    return extensionApi.runtime.lastError;
  } catch (_error) {
    return null;
  }
};

const hasExtensionContext = () => {
  try {
    return extensionContextActive && Boolean(extensionApi?.runtime?.id);
  } catch (_error) {
    return false;
  }
};

const disableExtensionContext = () => {
  extensionContextActive = false;
  window.clearInterval(refreshTimer);
  window.clearTimeout(scanTimer);
};

const isContextInvalidatedError = (error) =>
  normalizeText(error?.message || error).includes("Extension context invalidated");

const loadPluginSettings = () =>
  new Promise((resolve) => {
    if (!hasExtensionContext()) {
      resolve();
      return;
    }

    try {
      extensionApi.storage.sync.get(
        { frontendBaseUrl: DEFAULT_FRONTEND_URL },
        (settings) => {
          const error = getRuntimeError();
          if (error) {
            if (isContextInvalidatedError(error)) {
              disableExtensionContext();
            }
            resolve();
            return;
          }

          maxOpsUiBaseUrl = normalizeLocalBaseUrl(
            settings.frontendBaseUrl,
            DEFAULT_FRONTEND_URL,
          );
          resolve();
        },
      );
    } catch (error) {
      if (isContextInvalidatedError(error)) {
        disableExtensionContext();
      }
      resolve();
    }
  });

const safeSendMessage = (message, onSuccess) => {
  if (!hasExtensionContext()) {
    return;
  }

  try {
    extensionApi.runtime.sendMessage(message, (response) => {
      const error = getRuntimeError();
      if (error) {
        if (isContextInvalidatedError(error)) {
          disableExtensionContext();
        }
        return;
      }

      if (response?.ok) {
        onSuccess(response.payload);
      }
    });
  } catch (error) {
    if (isContextInvalidatedError(error)) {
      disableExtensionContext();
    }
  }
};

const withMaxOpsDomUpdate = (callback) => {
  isApplyingMaxOpsDom = true;
  try {
    return callback();
  } finally {
    window.setTimeout(() => {
      isApplyingMaxOpsDom = false;
    }, 0);
  }
};

const getInstanceIdFromText = (text) => {
  const matches = normalizeText(text).match(INSTANCE_ID_PATTERN);
  return matches ? matches[0] : null;
};

const getElementInstanceId = (element) => {
  const ownText = Array.from(element.childNodes)
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent)
    .join(" ");
  const directMatch = getInstanceIdFromText(ownText);
  if (directMatch) {
    return directMatch;
  }

  const fullText = normalizeText(element.textContent);
  if (fullText.length > 240) {
    return null;
  }

  return getInstanceIdFromText(fullText);
};

const getMonthlyCost = (resource) =>
  toNumber(
    resource?.workload?.monthly_cost_estimate ??
      resource?.metadata?.monthly_cost_estimate ??
      resource?.monthly_cost_estimate,
  );

const getMonthlySavings = (resource) =>
  toNumber(resource?.maxops?.potential_savings_monthly);

const getYearlySavings = (resource) => {
  const yearly = resource?.maxops?.potential_savings_yearly;
  if (yearly !== undefined && yearly !== null) {
    return toNumber(yearly);
  }

  return getMonthlySavings(resource) * 12;
};

const getFindingLabel = (resource) => {
  const maxops = resource?.maxops || {};
  if (maxops.status === "healthy" && !maxops.finding_type) {
    return "Healthy";
  }

  return maxops.title || maxops.finding_type || "Check failure";
};

const getSeverityClass = (resource) => {
  const severity = normalizeText(resource?.maxops?.severity).toLowerCase();
  if (["critical", "high"].includes(severity)) {
    return "maxops-badge--risk";
  }
  if (["medium", "low"].includes(severity)) {
    return "maxops-badge--warn";
  }
  if (resource?.maxops?.status === "healthy") {
    return "maxops-badge--healthy";
  }
  return "maxops-badge--warn";
};

const buildOverlay = (instance) => {
  const wrapper = document.createElement("span");
  wrapper.className = "maxops-overlay";
  wrapper.setAttribute(OVERLAY_ATTR, "true");

  const cost = document.createElement("span");
  cost.className = "maxops-chip";
  cost.textContent = `${formatCurrency(getMonthlyCost(instance))}/mo`;

  const savings = document.createElement("span");
  savings.className = "maxops-chip maxops-chip--savings";
  savings.textContent = `${formatCurrency(getMonthlySavings(instance))} save/mo`;

  const finding = document.createElement("span");
  finding.className = `maxops-chip ${getSeverityClass(instance)}`;
  finding.textContent = getFindingLabel(instance);

  wrapper.append(cost, savings, finding);
  return wrapper;
};

const removeStaleOverlays = () => {
  document
    .querySelectorAll(`[${OVERLAY_ATTR}="true"]`)
    .forEach((element) => {
      const parent = element.parentElement;
      element.remove();
      if (parent) {
        parent.removeAttribute(SOURCE_ATTR);
      }
    });
};

const findBestAnchor = (element) => {
  const cell = element.closest("[role='gridcell'], td, th");
  if (cell) {
    return cell;
  }

  const link = element.closest("a");
  return link || element;
};

const addOverlayToAnchor = (anchor, instanceId, instance) => {
  if (!anchor || anchor.getAttribute(SOURCE_ATTR) === instanceId) {
    return false;
  }

  const overlay = buildOverlay(instance);
  anchor.appendChild(overlay);
  anchor.setAttribute(SOURCE_ATTR, instanceId);
  return true;
};

const isDynamoDbConsolePage = () =>
  window.location.href.includes("/dynamodbv2/");

const getAwsRegion = () => {
  try {
    const url = new URL(window.location.href);
    const region = url.searchParams.get("region");
    if (region) {
      return region;
    }
  } catch (_error) {
    // Ignore malformed URLs and fall back to host parsing.
  }

  const hostMatch = window.location.hostname.match(
    /^([a-z]{2}-[a-z-]+-\d+)\.console\.aws\.amazon\.com$/i,
  );
  return hostMatch ? hostMatch[1] : null;
};

const getDynamoDbResources = (overview) => {
  if (Array.isArray(overview?.resources)) {
    return overview.resources;
  }
  if (Array.isArray(overview?.tables)) {
    return overview.tables;
  }
  if (Array.isArray(overview?.items)) {
    return overview.items;
  }
  return [];
};

const getDynamoDbTableName = (resource) => {
  const candidates = [
    resource?.table_name,
    resource?.metadata?.table_name,
    resource?.resource_name,
    resource?.name,
  ];

  for (const candidate of candidates) {
    const value = normalizeText(candidate);
    if (value) {
      return value.split("/")[0];
    }
  }

  const resourceId = normalizeText(resource?.resource_id);
  return resourceId ? resourceId.split("/")[0] : null;
};

const buildDynamoDbTableMap = (overview, region) => {
  const tables = new Map();
  for (const resource of getDynamoDbResources(overview)) {
    if (region && normalizeText(resource?.region) !== region) {
      continue;
    }

    const tableName = getDynamoDbTableName(resource);
    if (!tableName) {
      continue;
    }

    const existing =
      tables.get(tableName) ||
      {
        tableName,
        monthlyCost: 0,
        monthlySavings: 0,
        yearlySavings: 0,
        findings: 0,
        findingLabel: null,
        detailResourceId: null,
      };

    existing.monthlyCost += getMonthlyCost(resource);
    existing.monthlySavings += getMonthlySavings(resource);
    existing.yearlySavings += getYearlySavings(resource);
    if (!existing.detailResourceId || resource?.resource_type === "dynamodb_table") {
      existing.detailResourceId = normalizeText(resource?.resource_id) || tableName;
    }

    const maxops = resource?.maxops || {};
    if (maxops.status && maxops.status !== "healthy") {
      existing.findings += 1;
      existing.findingLabel ||= getFindingLabel(resource);
    }

    tables.set(tableName, existing);
  }

  return tables;
};

const findDynamoDbPresentationTables = () =>
  Array.from(
    document.querySelectorAll(
      "table[role='presentation'][class^='awsui_table'], table[role='presentation'][class*=' awsui_table']",
    ),
  );

const findDynamoDbBodyTables = () =>
  Array.from(
    document.querySelectorAll(
      "table[role='table'][aria-label='Tables'][class^='awsui_table'], table[role='table'][aria-label='Tables'][class*=' awsui_table']",
    ),
  );

const buildDynamoDbHeaderCell = (referenceHeader, column) => {
  const header = document.createElement("th");
  header.setAttribute(DDB_HEADER_ATTR, "true");
  header.setAttribute(DDB_COLUMN_ATTR, column.key);
  header.setAttribute("scope", "col");
  header.className = `${referenceHeader?.className || ""} maxops-ddb-header-cell`.trim();
  header.style.width = column.width;

  const referenceContent = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-content']",
  );
  const content = document.createElement("div");
  content.className = `${referenceContent?.className || ""} maxops-ddb-header-content`.trim();

  const referenceText = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-text']",
  );
  const text = document.createElement("div");
  text.className = `${referenceText?.className || ""} maxops-ddb-header-text`.trim();
  text.textContent = column.label;

  content.appendChild(text);
  header.appendChild(content);
  return header;
};

const removeDynamoDbColumns = (root) => {
  root
    .querySelectorAll(`[${DDB_HEADER_ATTR}="true"], [${DDB_CELL_ATTR}="true"]`)
    .forEach((element) => element.remove());
};

const ensureDynamoDbHeaders = (table) => {
  const row = table.querySelector("thead tr");
  if (!row) {
    return;
  }

  const existingColumns = Array.from(
    row.querySelectorAll(`[${DDB_HEADER_ATTR}="true"]`),
  ).map((cell) => cell.getAttribute(DDB_COLUMN_ATTR));
  const expectedColumns = DDB_COLUMNS.map((column) => column.key);
  if (
    existingColumns.length === expectedColumns.length &&
    expectedColumns.every((column) => existingColumns.includes(column))
  ) {
    return;
  }

  removeDynamoDbColumns(table);
  const referenceHeader = Array.from(row.children)
    .reverse()
    .find((cell) => cell.tagName === "TH");
  for (const column of DDB_COLUMNS) {
    row.appendChild(buildDynamoDbHeaderCell(referenceHeader, column));
  }
};

const getRowDynamoDbTableName = (row) => {
  const tableLink = row.querySelector("a[href*='#table?name=']");
  if (tableLink) {
    const href = tableLink.getAttribute("href") || "";
    const query = href.split("?")[1] || "";
    const name = new URLSearchParams(query).get("name");
    return normalizeText(name || tableLink.textContent);
  }

  const label = row.querySelector("label[aria-label]");
  return normalizeText(label?.getAttribute("aria-label"));
};

const buildMaxOpsResourceUrl = (summary) => {
  const resourceId = normalizeText(summary?.detailResourceId || summary?.tableName);
  const path = resourceId
    ? `/dashboard/resources/dynamodb/resources/${encodeURIComponent(resourceId)}`
    : "/dashboard/resources/dynamodb";
  return `${maxOpsUiBaseUrl}${path}`;
};

const getDynamoDbColumnText = (columnKey, summary) => {
  if (!summary) {
    return "No data";
  }

  if (columnKey === "price") {
    return `${formatCurrency(summary.monthlyCost)}/mo`;
  }
  if (columnKey === "savings") {
    return `${formatCurrency(summary.yearlySavings)}/yr`;
  }
  if (summary.findings === 0) {
    return "Healthy";
  }
  return summary.findings === 1
    ? summary.findingLabel || "1 finding"
    : `${summary.findings} findings`;
};

const buildDynamoDbValue = (column, summary) => {
  const link = document.createElement("a");
  link.className = "maxops-ddb-link";
  link.href = buildMaxOpsResourceUrl(summary);
  link.target = "_blank";
  link.rel = "noreferrer";
  link.title = summary
    ? `Open ${summary.tableName} in MaxOps`
    : "Open DynamoDB inventory in MaxOps";
  link.textContent = getDynamoDbColumnText(column.key, summary);
  return link;
};

const ensureDynamoDbCell = (row, column, summary) => {
  let cell = row.querySelector(
    `[${DDB_CELL_ATTR}="true"][${DDB_COLUMN_ATTR}="${column.key}"]`,
  );
  if (!cell) {
    const referenceCell = Array.from(row.children)
      .reverse()
      .find((element) => element.tagName === "TD");
    cell = document.createElement("td");
    cell.setAttribute(DDB_CELL_ATTR, "true");
    cell.setAttribute(DDB_COLUMN_ATTR, column.key);
    cell.className = `${referenceCell?.className || ""} maxops-ddb-cell`.trim();
    row.appendChild(cell);
  }

  let content = cell.querySelector(`[${DDB_CONTENT_ATTR}="true"]`);
  if (!content) {
    const referenceContent = row.querySelector(
      "td div[class*='awsui_body-cell-content'], td div[class^='awsui_content'], td div[class*=' awsui_content']",
    );
    content = document.createElement("div");
    content.setAttribute(DDB_CONTENT_ATTR, "true");
    content.className = `${referenceContent?.className || ""} maxops-ddb-cell-content`.trim();
    cell.appendChild(content);
  }

  content.replaceChildren(buildDynamoDbValue(column, summary));
};

const scanDynamoDbConsole = () => {
  if (!isDynamoDbConsolePage() || !cachedDynamoDbOverview) {
    return;
  }

  const presentationTables = findDynamoDbPresentationTables();
  const bodyTables = findDynamoDbBodyTables();
  if (presentationTables.length === 0 && bodyTables.length === 0) {
    return;
  }

  const region = getAwsRegion();
  cachedDynamoDbTables = buildDynamoDbTableMap(cachedDynamoDbOverview, region);

  for (const table of [...presentationTables, ...bodyTables]) {
    ensureDynamoDbHeaders(table);
  }

  for (const table of bodyTables) {
    const rows = table.querySelectorAll("tbody tr");
    for (const row of rows) {
      const tableName = getRowDynamoDbTableName(row);
      if (!tableName) {
        continue;
      }
      const summary = cachedDynamoDbTables.get(tableName);
      for (const column of DDB_COLUMNS) {
        ensureDynamoDbCell(row, column, summary);
      }
    }
  }
};

const isEc2ConsolePage = () => {
  const href = window.location.href.toLowerCase();
  return href.includes("/ec2/") && (href.includes("instances") || href.includes("#instances"));
};

const hasEc2InstancesTableContext = () =>
  Array.from(document.querySelectorAll("h1, h2, [aria-labelledby]")).some((element) =>
    normalizeText(element.textContent).toLowerCase().includes("instances"),
  );

const getEc2Instances = (overview) => {
  if (Array.isArray(overview?.instances)) {
    return overview.instances;
  }
  if (Array.isArray(overview?.resources)) {
    return overview.resources;
  }
  return [];
};

const buildEc2InstanceMap = (overview, region) => {
  const instances = new Map();
  for (const instance of getEc2Instances(overview)) {
    if (region && normalizeText(instance?.region) !== region) {
      continue;
    }

    const instanceId = normalizeText(instance?.resource_id);
    if (!instanceId) {
      continue;
    }

    instances.set(instanceId, {
      instanceId,
      detailResourceId: instanceId,
      monthlyCost: getMonthlyCost(instance),
      monthlySavings: getMonthlySavings(instance),
      yearlySavings: getYearlySavings(instance),
      findings: instance?.maxops?.status && instance.maxops.status !== "healthy" ? 1 : 0,
      findingLabel: getFindingLabel(instance),
    });
  }
  return instances;
};

const hasEc2InstanceIdHeader = (table) =>
  Array.from(table.querySelectorAll("thead th")).some(
    (cell) =>
      getAwsUiColumnId(cell) === "instanceId" ||
      normalizeText(cell.textContent).toLowerCase() === "instance id",
  );

const findEc2BodyTables = () =>
  findAwsUiTables().filter((table) =>
    table.querySelector("tbody tr") &&
    (hasEc2InstanceIdHeader(table) ||
      Array.from(table.querySelectorAll("tbody tr")).some((row) =>
        Boolean(getRowEc2InstanceId(row)),
      )),
  );

const findEc2PresentationTables = () =>
  findAwsUiTables().filter(
    (table) => !table.querySelector("tbody tr") && hasEc2InstanceIdHeader(table),
  );

const buildEc2HeaderCell = (referenceHeader, column) => {
  const header = document.createElement("th");
  header.setAttribute(EC2_HEADER_ATTR, "true");
  header.setAttribute(EC2_COLUMN_ATTR, column.key);
  header.setAttribute("scope", "col");
  header.className = `${referenceHeader?.className || ""} maxops-ddb-header-cell`.trim();
  header.style.width = column.width;

  const referenceContent = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-content']",
  );
  const content = document.createElement("div");
  content.className = `${referenceContent?.className || ""} maxops-ddb-header-content`.trim();

  const referenceText = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-text']",
  );
  const text = document.createElement("div");
  text.className = `${referenceText?.className || ""} maxops-ddb-header-text`.trim();
  text.textContent = column.label;

  content.appendChild(text);
  header.appendChild(content);
  return header;
};

const findEc2InstanceIdHeader = (row) =>
  Array.from(row.children).find((cell) => getAwsUiColumnId(cell) === "instanceId") ||
  Array.from(row.children).find((cell) =>
    normalizeText(cell.textContent).toLowerCase() === "instance id",
  );

const findRowEc2InstanceIdCell = (row) =>
  Array.from(row.children).find((cell) => getAwsUiColumnId(cell) === "instanceId") ||
  Array.from(row.children).find((cell) =>
    Boolean(getInstanceIdFromText(cell.textContent)),
  );

const removeEc2Columns = (root) => {
  root
    .querySelectorAll(`[${EC2_HEADER_ATTR}="true"], [${EC2_CELL_ATTR}="true"]`)
    .forEach((element) => element.remove());
};

const ensureEc2Headers = (table) => {
  const row = table.querySelector("thead tr");
  if (!row) {
    return;
  }

  const existingColumns = Array.from(
    row.querySelectorAll(`[${EC2_HEADER_ATTR}="true"]`),
  ).map((cell) => cell.getAttribute(EC2_COLUMN_ATTR));
  const expectedColumns = EC2_COLUMNS.map((column) => column.key);
  const instanceIdHeader = findEc2InstanceIdHeader(row);
  const firstMaxOpsHeader = row.querySelector(
    `[${EC2_HEADER_ATTR}="true"][${EC2_COLUMN_ATTR}="${expectedColumns[0]}"]`,
  );
  if (
    existingColumns.length === expectedColumns.length &&
    expectedColumns.every((column) => existingColumns.includes(column)) &&
    (!instanceIdHeader || instanceIdHeader.nextElementSibling === firstMaxOpsHeader)
  ) {
    return;
  }

  removeEc2Columns(table);
  const referenceHeader = Array.from(row.children)
    .reverse()
    .find((cell) => cell.tagName === "TH");
  let previousCell = instanceIdHeader;
  for (const column of EC2_COLUMNS) {
    const headerCell = buildEc2HeaderCell(referenceHeader, column);
    if (previousCell) {
      previousCell.after(headerCell);
    } else {
      row.appendChild(headerCell);
    }
    previousCell = headerCell;
  }
};

const getRowEc2InstanceId = (row) => {
  const link = row.querySelector("a[href*='InstanceDetails:instanceId='], a[href*='instanceId=i-'], a[href*='i-']");
  const linkMatch = getInstanceIdFromText(
    `${link?.textContent || ""} ${link?.getAttribute("href") || ""}`,
  );
  if (linkMatch) {
    return linkMatch;
  }

  const label = row.querySelector("label[aria-label]");
  const labelMatch = getInstanceIdFromText(label?.getAttribute("aria-label"));
  if (labelMatch) {
    return labelMatch;
  }

  return getInstanceIdFromText(row.textContent);
};

const buildMaxOpsEc2ResourceUrl = (summary) => {
  const resourceId = normalizeText(summary?.detailResourceId || summary?.instanceId);
  const path = resourceId
    ? `/dashboard/resources/ec2/instances/${encodeURIComponent(resourceId)}`
    : "/dashboard/resources/ec2";
  return `${maxOpsUiBaseUrl}${path}`;
};

const getEc2ColumnText = (columnKey, summary) => {
  if (!summary) {
    return "No data";
  }
  if (columnKey === "price") {
    return `${formatCurrency(summary.monthlyCost)}/mo`;
  }
  if (columnKey === "savings") {
    return `${formatCurrency(summary.yearlySavings)}/yr`;
  }
  if (summary.findings === 0) {
    return "Healthy";
  }
  return summary.findingLabel || "1 finding";
};

const buildEc2Value = (column, summary) => {
  const link = document.createElement("a");
  link.className = "maxops-ddb-link";
  link.href = buildMaxOpsEc2ResourceUrl(summary);
  link.target = "_blank";
  link.rel = "noreferrer";
  link.title = summary
    ? `Open ${summary.instanceId} in MaxOps`
    : "Open EC2 inventory in MaxOps";
  link.textContent = getEc2ColumnText(column.key, summary);
  return link;
};

const ensureEc2Cell = (row, column, summary) => {
  let cell = row.querySelector(
    `[${EC2_CELL_ATTR}="true"][${EC2_COLUMN_ATTR}="${column.key}"]`,
  );
  if (!cell) {
    const referenceCell = Array.from(row.children)
      .reverse()
      .find((element) => element.tagName === "TD");
    cell = document.createElement("td");
    cell.setAttribute(EC2_CELL_ATTR, "true");
    cell.setAttribute(EC2_COLUMN_ATTR, column.key);
    cell.className = `${referenceCell?.className || ""} maxops-ddb-cell`.trim();
  }

  const previousMaxOpsCell =
    column.key === EC2_COLUMNS[0].key
      ? null
      : row.querySelector(
          `[${EC2_CELL_ATTR}="true"][${EC2_COLUMN_ATTR}="${
            EC2_COLUMNS[EC2_COLUMNS.findIndex((item) => item.key === column.key) - 1]?.key
          }"]`,
        );
  const insertAfter = previousMaxOpsCell || findRowEc2InstanceIdCell(row);
  if (insertAfter && insertAfter.nextSibling !== cell) {
    insertAfter.after(cell);
  } else if (!insertAfter && !cell.parentElement) {
    row.appendChild(cell);
  }

  let content = cell.querySelector(`[${EC2_CONTENT_ATTR}="true"]`);
  if (!content) {
    const referenceContent = row.querySelector(
      "td div[class*='awsui_body-cell-content'], td div[class^='awsui_content'], td div[class*=' awsui_content']",
    );
    content = document.createElement("div");
    content.setAttribute(EC2_CONTENT_ATTR, "true");
    content.className = `${referenceContent?.className || ""} maxops-ddb-cell-content`.trim();
    cell.appendChild(content);
  }

  content.replaceChildren(buildEc2Value(column, summary));
};

const scanEc2Console = () => {
  const bodyTables = findEc2BodyTables();
  if (bodyTables.length === 0) {
    return;
  }

  if (!isEc2ConsolePage() && !hasEc2InstancesTableContext()) {
    return;
  }

  removeStaleOverlays();

  const presentationTables = findEc2PresentationTables();
  const region = getAwsRegion();
  cachedInstances = cachedEc2Overview
    ? buildEc2InstanceMap(cachedEc2Overview, region)
    : new Map();

  for (const table of [...presentationTables, ...bodyTables]) {
    ensureEc2Headers(table);
  }

  for (const table of bodyTables) {
    const rows = table.querySelectorAll("tbody tr");
    for (const row of rows) {
      const instanceId = getRowEc2InstanceId(row);
      if (!instanceId) {
        continue;
      }
      const summary = cachedInstances.get(instanceId);
      for (const column of EC2_COLUMNS) {
        ensureEc2Cell(row, column, summary);
      }
    }
  }
};

const isRdsConsolePage = () => {
  const href = window.location.href.toLowerCase();
  return href.includes("/rds/") && (href.includes("database") || href.includes("databases"));
};

const hasRdsDatabasesTableContext = () =>
  Array.from(document.querySelectorAll("h1, h2, [aria-labelledby]")).some((element) => {
    const text = normalizeText(element.textContent).toLowerCase();
    return text.includes("databases") || text.includes("db instances");
  });

const getRdsInstances = (overview) => {
  if (Array.isArray(overview?.instances)) {
    return overview.instances;
  }
  if (Array.isArray(overview?.resources)) {
    return overview.resources;
  }
  return [];
};

const getRdsIdentifierCandidates = (instance) => {
  const resourceId = normalizeText(instance?.resource_id);
  const arnMatch = resourceId.match(/:db:([^:/]+)$/);
  return [
    resourceId,
    arnMatch ? arnMatch[1] : null,
    instance?.resource_name,
    instance?.metadata?.db_instance_identifier,
    instance?.metadata?.dbInstanceIdentifier,
    instance?.metadata?.DBInstanceIdentifier,
    instance?.aws_payload?.DBInstanceIdentifier,
  ]
    .map((value) => normalizeText(value))
    .filter(Boolean);
};

const buildRdsInstanceMap = (overview, region) => {
  const instances = new Map();
  for (const instance of getRdsInstances(overview)) {
    if (region && normalizeText(instance?.region) !== region) {
      continue;
    }

    const identifiers = getRdsIdentifierCandidates(instance);
    if (identifiers.length === 0) {
      continue;
    }

    const resourceId = normalizeText(instance?.resource_id) || identifiers[0];
    const summary = {
      instanceId: identifiers[0],
      detailResourceId: resourceId,
      monthlyCost: getMonthlyCost(instance),
      monthlySavings: getMonthlySavings(instance),
      yearlySavings: getYearlySavings(instance),
      findings: instance?.maxops?.status && instance.maxops.status !== "healthy" ? 1 : 0,
      findingLabel: getFindingLabel(instance),
    };

    for (const identifier of identifiers) {
      instances.set(identifier, summary);
    }
  }
  return instances;
};

const isRdsIdentifierColumnId = (columnId) =>
  [
    "db",
    "db-id",
    "db-name",
    "database",
    "database-id",
    "database-name",
    "db-instance",
    "db-instance-id",
    "db-instance-identifier",
    "dbInstanceIdentifier",
    "DBInstanceIdentifier",
  ].includes(columnId);

const isRdsIdentifierHeaderText = (text) => {
  const normalized = normalizeText(text).toLowerCase();
  return (
    normalized === "db identifier" ||
    normalized === "db instance identifier" ||
    normalized === "database" ||
    normalized === "databases" ||
    normalized === "db instance"
  );
};

const hasRdsIdentifierHeader = (table) =>
  Array.from(table.querySelectorAll("thead th")).some(
    (cell) =>
      isRdsIdentifierColumnId(getAwsUiColumnId(cell)) ||
      isRdsIdentifierHeaderText(cell.textContent),
  );

const findRdsBodyTables = () =>
  findAwsUiTables().filter((table) =>
    table.querySelector("tbody tr") &&
    (hasRdsIdentifierHeader(table) ||
      Array.from(table.querySelectorAll("tbody tr")).some((row) =>
        Boolean(getRowRdsIdentifier(row)),
      )),
  );

const findRdsPresentationTables = () =>
  findAwsUiTables().filter(
    (table) => !table.querySelector("tbody tr") && hasRdsIdentifierHeader(table),
  );

const buildRdsHeaderCell = (referenceHeader, column) => {
  const header = document.createElement("th");
  header.setAttribute(RDS_HEADER_ATTR, "true");
  header.setAttribute(RDS_COLUMN_ATTR, column.key);
  header.setAttribute("scope", "col");
  header.className = `${referenceHeader?.className || ""} maxops-ddb-header-cell`.trim();
  header.style.width = column.width;

  const referenceContent = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-content']",
  );
  const content = document.createElement("div");
  content.className = `${referenceContent?.className || ""} maxops-ddb-header-content`.trim();

  const referenceText = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-text']",
  );
  const text = document.createElement("div");
  text.className = `${referenceText?.className || ""} maxops-ddb-header-text`.trim();
  text.textContent = column.label;

  content.appendChild(text);
  header.appendChild(content);
  return header;
};

const findRdsIdentifierHeader = (row) =>
  Array.from(row.children).find((cell) => isRdsIdentifierColumnId(getAwsUiColumnId(cell))) ||
  Array.from(row.children).find((cell) => isRdsIdentifierHeaderText(cell.textContent));

const findRowRdsIdentifierCell = (row) =>
  Array.from(row.children).find((cell) => isRdsIdentifierColumnId(getAwsUiColumnId(cell))) ||
  Array.from(row.children).find((cell) => cachedRdsInstances.has(normalizeText(cell.textContent))) ||
  Array.from(row.children).find((cell) =>
    Array.from(cell.querySelectorAll("a")).some((link) =>
      cachedRdsInstances.has(normalizeText(link.textContent)),
    ),
  );

const removeRdsColumns = (root) => {
  root
    .querySelectorAll(`[${RDS_HEADER_ATTR}="true"], [${RDS_CELL_ATTR}="true"]`)
    .forEach((element) => element.remove());
};

const ensureRdsHeaders = (table) => {
  const row = table.querySelector("thead tr");
  if (!row) {
    return;
  }

  const existingColumns = Array.from(
    row.querySelectorAll(`[${RDS_HEADER_ATTR}="true"]`),
  ).map((cell) => cell.getAttribute(RDS_COLUMN_ATTR));
  const expectedColumns = RDS_COLUMNS.map((column) => column.key);
  const identifierHeader = findRdsIdentifierHeader(row);
  const firstMaxOpsHeader = row.querySelector(
    `[${RDS_HEADER_ATTR}="true"][${RDS_COLUMN_ATTR}="${expectedColumns[0]}"]`,
  );
  if (
    existingColumns.length === expectedColumns.length &&
    expectedColumns.every((column) => existingColumns.includes(column)) &&
    (!identifierHeader || identifierHeader.nextElementSibling === firstMaxOpsHeader)
  ) {
    return;
  }

  removeRdsColumns(table);
  const referenceHeader = Array.from(row.children)
    .reverse()
    .find((cell) => cell.tagName === "TH");
  let previousCell = identifierHeader;
  for (const column of RDS_COLUMNS) {
    const headerCell = buildRdsHeaderCell(referenceHeader, column);
    if (previousCell) {
      previousCell.after(headerCell);
    } else {
      row.appendChild(headerCell);
    }
    previousCell = headerCell;
  }
};

const getRowRdsIdentifier = (row) => {
  const identifierCell = findRowRdsIdentifierCell(row);
  const candidates = [
    ...Array.from(identifierCell?.querySelectorAll("a") || []).map((link) => link.textContent),
    identifierCell?.textContent,
    row.querySelector("label[aria-label]")?.getAttribute("aria-label"),
  ];

  for (const candidate of candidates) {
    const value = normalizeText(candidate);
    if (cachedRdsInstances.has(value)) {
      return value;
    }
  }

  return normalizeText(candidates.find(Boolean));
};

const buildMaxOpsRdsResourceUrl = (summary) => {
  const resourceId = normalizeText(summary?.detailResourceId || summary?.instanceId);
  const path = resourceId
    ? `/dashboard/resources/rds/instances/${encodeURIComponent(resourceId)}`
    : "/dashboard/resources/rds";
  return `${maxOpsUiBaseUrl}${path}`;
};

const getRdsColumnText = (columnKey, summary) => {
  if (!summary) {
    return "No data";
  }
  if (columnKey === "price") {
    return `${formatCurrency(summary.monthlyCost)}/mo`;
  }
  if (columnKey === "savings") {
    return `${formatCurrency(summary.yearlySavings)}/yr`;
  }
  if (summary.findings === 0) {
    return "Healthy";
  }
  return summary.findingLabel || "1 finding";
};

const buildRdsValue = (column, summary) => {
  const link = document.createElement("a");
  link.className = "maxops-ddb-link";
  link.href = buildMaxOpsRdsResourceUrl(summary);
  link.target = "_blank";
  link.rel = "noreferrer";
  link.title = summary
    ? `Open ${summary.instanceId} in MaxOps`
    : "Open RDS inventory in MaxOps";
  link.textContent = getRdsColumnText(column.key, summary);
  return link;
};

const ensureRdsCell = (row, column, summary) => {
  let cell = row.querySelector(
    `[${RDS_CELL_ATTR}="true"][${RDS_COLUMN_ATTR}="${column.key}"]`,
  );
  if (!cell) {
    const referenceCell = Array.from(row.children)
      .reverse()
      .find((element) => element.tagName === "TD");
    cell = document.createElement("td");
    cell.setAttribute(RDS_CELL_ATTR, "true");
    cell.setAttribute(RDS_COLUMN_ATTR, column.key);
    cell.className = `${referenceCell?.className || ""} maxops-ddb-cell`.trim();
  }

  const previousMaxOpsCell =
    column.key === RDS_COLUMNS[0].key
      ? null
      : row.querySelector(
          `[${RDS_CELL_ATTR}="true"][${RDS_COLUMN_ATTR}="${
            RDS_COLUMNS[RDS_COLUMNS.findIndex((item) => item.key === column.key) - 1]?.key
          }"]`,
        );
  const insertAfter = previousMaxOpsCell || findRowRdsIdentifierCell(row);
  if (insertAfter && insertAfter.nextSibling !== cell) {
    insertAfter.after(cell);
  } else if (!insertAfter && !cell.parentElement) {
    row.appendChild(cell);
  }

  let content = cell.querySelector(`[${RDS_CONTENT_ATTR}="true"]`);
  if (!content) {
    const referenceContent = row.querySelector(
      "td div[class*='awsui_body-cell-content'], td div[class^='awsui_content'], td div[class*=' awsui_content']",
    );
    content = document.createElement("div");
    content.setAttribute(RDS_CONTENT_ATTR, "true");
    content.className = `${referenceContent?.className || ""} maxops-ddb-cell-content`.trim();
    cell.appendChild(content);
  }

  content.replaceChildren(buildRdsValue(column, summary));
};

const scanRdsConsole = () => {
  const region = getAwsRegion();
  cachedRdsInstances = cachedRdsOverview
    ? buildRdsInstanceMap(cachedRdsOverview, region)
    : new Map();

  const bodyTables = findRdsBodyTables();
  if (bodyTables.length === 0) {
    return;
  }

  if (!isRdsConsolePage() && !hasRdsDatabasesTableContext()) {
    return;
  }

  const presentationTables = findRdsPresentationTables();
  for (const table of [...presentationTables, ...bodyTables]) {
    ensureRdsHeaders(table);
  }

  for (const table of bodyTables) {
    const rows = table.querySelectorAll("tbody tr");
    for (const row of rows) {
      const identifier = getRowRdsIdentifier(row);
      if (!identifier) {
        continue;
      }
      const summary = cachedRdsInstances.get(identifier);
      for (const column of RDS_COLUMNS) {
        ensureRdsCell(row, column, summary);
      }
    }
  }
};

const isElasticacheConsolePage = () => {
  const href = window.location.href.toLowerCase();
  return (
    href.includes("/elasticache/") &&
    (href.includes("redis") ||
      href.includes("valkey") ||
      href.includes("memcached") ||
      href.includes("cluster"))
  );
};

const hasElasticacheTableContext = () =>
  Array.from(document.querySelectorAll("h1, h2, [aria-labelledby]")).some((element) => {
    const text = normalizeText(element.textContent).toLowerCase();
    return text.includes("elasticache") || text.includes("redis") || text.includes("valkey");
  });

const getElasticacheResources = (overview) => {
  if (Array.isArray(overview?.resources)) {
    return overview.resources;
  }
  if (Array.isArray(overview?.instances)) {
    return overview.instances;
  }
  return [];
};

const getElasticacheIdentifierCandidates = (resource) => {
  const resourceId = normalizeText(resource?.resource_id);
  return [
    resourceId,
    resource?.resource_name,
    resource?.metadata?.cache_cluster_id,
    resource?.metadata?.cacheClusterId,
    resource?.metadata?.CacheClusterId,
    resource?.metadata?.replication_group_id,
    resource?.metadata?.replicationGroupId,
    resource?.metadata?.ReplicationGroupId,
    resource?.aws_payload?.CacheClusterId,
    resource?.aws_payload?.ReplicationGroupId,
  ]
    .map((value) => normalizeText(value))
    .filter(Boolean);
};

const buildElasticacheResourceMap = (overview, region) => {
  const resources = new Map();
  for (const resource of getElasticacheResources(overview)) {
    if (region && normalizeText(resource?.region) !== region) {
      continue;
    }

    const identifiers = getElasticacheIdentifierCandidates(resource);
    if (identifiers.length === 0) {
      continue;
    }

    const detailResourceId = normalizeText(resource?.resource_id) || identifiers[0];
    const summary = {
      resourceId: identifiers[0],
      detailResourceId,
      monthlyCost: getMonthlyCost(resource),
      monthlySavings: getMonthlySavings(resource),
      yearlySavings: getYearlySavings(resource),
      findings: resource?.maxops?.status && resource.maxops.status !== "healthy" ? 1 : 0,
      findingLabel: getFindingLabel(resource),
    };

    for (const identifier of identifiers) {
      resources.set(identifier, summary);
    }
  }
  return resources;
};

const isElasticacheIdentifierColumnId = (columnId) => {
  const normalized = normalizeText(columnId).toLowerCase();
  return (
    normalized === "name" ||
    normalized.includes("cache-name") ||
    normalized.includes("cache_name") ||
    normalized.includes("cachename") ||
    normalized.includes("cacheclusterid") ||
    normalized.includes("cache-cluster-id") ||
    normalized.includes("cache_cluster_id") ||
    normalized.includes("replicationgroupid") ||
    normalized.includes("replication-group-id") ||
    normalized.includes("replication_group_id") ||
    normalized.includes("clusterid")
  );
};

const isElasticacheIdentifierHeaderText = (text) => {
  const normalized = normalizeText(text).toLowerCase();
  return (
    normalized === "cache name" ||
    normalized === "cluster name" ||
    normalized === "cluster id" ||
    normalized === "cache cluster id" ||
    normalized === "replication group id" ||
    normalized === "id" ||
    normalized === "name"
  );
};

const hasElasticacheIdentifierHeader = (table) =>
  Array.from(table.querySelectorAll("thead th")).some(
    (cell) =>
      isElasticacheIdentifierColumnId(getAwsUiColumnId(cell)) ||
      isElasticacheIdentifierHeaderText(cell.textContent),
  );

const findElasticacheBodyTables = () =>
  findAwsUiTables().filter((table) =>
    table.querySelector("tbody tr") &&
    (hasElasticacheIdentifierHeader(table) ||
      Array.from(table.querySelectorAll("tbody tr")).some((row) =>
        Boolean(getRowElasticacheIdentifier(row)),
      )),
  );

const findElasticachePresentationTables = () =>
  findAwsUiTables().filter(
    (table) =>
      !table.querySelector("tbody tr") &&
      hasElasticacheIdentifierHeader(table),
  );

const buildElasticacheHeaderCell = (referenceHeader, column) => {
  const header = document.createElement("th");
  header.setAttribute(ELASTICACHE_HEADER_ATTR, "true");
  header.setAttribute(ELASTICACHE_COLUMN_ATTR, column.key);
  header.setAttribute("scope", "col");
  header.className = `${referenceHeader?.className || ""} maxops-ddb-header-cell`.trim();
  header.style.width = column.width;

  const referenceContent = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-content']",
  );
  const content = document.createElement("div");
  content.className = `${referenceContent?.className || ""} maxops-ddb-header-content`.trim();

  const referenceText = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-text']",
  );
  const text = document.createElement("div");
  text.className = `${referenceText?.className || ""} maxops-ddb-header-text`.trim();
  text.textContent = column.label;

  content.appendChild(text);
  header.appendChild(content);
  return header;
};

const findElasticacheIdentifierHeader = (row) =>
  Array.from(row.children).find((cell) =>
    isElasticacheIdentifierColumnId(getAwsUiColumnId(cell)),
  ) ||
  Array.from(row.children).find((cell) =>
    isElasticacheIdentifierHeaderText(cell.textContent),
  );

const findRowElasticacheIdentifierCell = (row) =>
  Array.from(row.children).find((cell) =>
    isElasticacheIdentifierColumnId(getAwsUiColumnId(cell)),
  ) ||
  Array.from(row.children).find((cell) =>
    cachedElasticacheResources.has(normalizeText(cell.textContent)),
  ) ||
  Array.from(row.children).find((cell) =>
    Array.from(cell.querySelectorAll("a")).some((link) =>
      cachedElasticacheResources.has(normalizeText(link.textContent)),
    ),
  );

const removeElasticacheColumns = (root) => {
  root
    .querySelectorAll(
      `[${ELASTICACHE_HEADER_ATTR}="true"], [${ELASTICACHE_CELL_ATTR}="true"]`,
    )
    .forEach((element) => element.remove());
};

const ensureElasticacheHeaders = (table) => {
  const row = table.querySelector("thead tr");
  if (!row) {
    return;
  }

  const existingColumns = Array.from(
    row.querySelectorAll(`[${ELASTICACHE_HEADER_ATTR}="true"]`),
  ).map((cell) => cell.getAttribute(ELASTICACHE_COLUMN_ATTR));
  const expectedColumns = ELASTICACHE_COLUMNS.map((column) => column.key);
  const identifierHeader = findElasticacheIdentifierHeader(row);
  const firstMaxOpsHeader = row.querySelector(
    `[${ELASTICACHE_HEADER_ATTR}="true"][${ELASTICACHE_COLUMN_ATTR}="${expectedColumns[0]}"]`,
  );
  if (
    existingColumns.length === expectedColumns.length &&
    expectedColumns.every((column) => existingColumns.includes(column)) &&
    (!identifierHeader || identifierHeader.nextElementSibling === firstMaxOpsHeader)
  ) {
    return;
  }

  removeElasticacheColumns(table);
  const referenceHeader = Array.from(row.children)
    .reverse()
    .find((cell) => cell.tagName === "TH");
  let previousCell = identifierHeader;
  for (const column of ELASTICACHE_COLUMNS) {
    const headerCell = buildElasticacheHeaderCell(referenceHeader, column);
    if (previousCell) {
      previousCell.after(headerCell);
    } else {
      row.appendChild(headerCell);
    }
    previousCell = headerCell;
  }
};

const getRowElasticacheIdentifier = (row) => {
  const identifierCell = findRowElasticacheIdentifierCell(row);
  const candidates = [
    ...Array.from(identifierCell?.querySelectorAll("a") || []).map((link) => link.textContent),
    identifierCell?.textContent,
    row.querySelector("label[aria-label]")?.getAttribute("aria-label"),
  ];

  for (const candidate of candidates) {
    const value = normalizeText(candidate);
    if (cachedElasticacheResources.has(value)) {
      return value;
    }
  }

  return normalizeText(candidates.find(Boolean));
};

const buildMaxOpsElasticacheResourceUrl = (summary) => {
  const resourceId = normalizeText(summary?.detailResourceId || summary?.resourceId);
  const path = resourceId
    ? `/dashboard/resources/elasticache/resources/${encodeURIComponent(resourceId)}`
    : "/dashboard/resources/elasticache";
  return `${maxOpsUiBaseUrl}${path}`;
};

const getElasticacheColumnText = (columnKey, summary) => {
  if (!summary) {
    return "No data";
  }
  if (columnKey === "price") {
    return `${formatCurrency(summary.monthlyCost)}/mo`;
  }
  if (columnKey === "savings") {
    return `${formatCurrency(summary.yearlySavings)}/yr`;
  }
  if (summary.findings === 0) {
    return "Healthy";
  }
  return summary.findingLabel || "1 finding";
};

const buildElasticacheValue = (column, summary) => {
  const link = document.createElement("a");
  link.className = "maxops-ddb-link";
  link.href = buildMaxOpsElasticacheResourceUrl(summary);
  link.target = "_blank";
  link.rel = "noreferrer";
  link.title = summary
    ? `Open ${summary.resourceId} in MaxOps`
    : "Open ElastiCache inventory in MaxOps";
  link.textContent = getElasticacheColumnText(column.key, summary);
  return link;
};

const ensureElasticacheCell = (row, column, summary) => {
  let cell = row.querySelector(
    `[${ELASTICACHE_CELL_ATTR}="true"][${ELASTICACHE_COLUMN_ATTR}="${column.key}"]`,
  );
  if (!cell) {
    const referenceCell = Array.from(row.children)
      .reverse()
      .find((element) => element.tagName === "TD");
    cell = document.createElement("td");
    cell.setAttribute(ELASTICACHE_CELL_ATTR, "true");
    cell.setAttribute(ELASTICACHE_COLUMN_ATTR, column.key);
    cell.className = `${referenceCell?.className || ""} maxops-ddb-cell`.trim();
  }

  const previousMaxOpsCell =
    column.key === ELASTICACHE_COLUMNS[0].key
      ? null
      : row.querySelector(
          `[${ELASTICACHE_CELL_ATTR}="true"][${ELASTICACHE_COLUMN_ATTR}="${
            ELASTICACHE_COLUMNS[
              ELASTICACHE_COLUMNS.findIndex((item) => item.key === column.key) - 1
            ]?.key
          }"]`,
        );
  const insertAfter = previousMaxOpsCell || findRowElasticacheIdentifierCell(row);
  if (insertAfter && insertAfter.nextSibling !== cell) {
    insertAfter.after(cell);
  } else if (!insertAfter && !cell.parentElement) {
    row.appendChild(cell);
  }

  let content = cell.querySelector(`[${ELASTICACHE_CONTENT_ATTR}="true"]`);
  if (!content) {
    const referenceContent = row.querySelector(
      "td div[class*='awsui_body-cell-content'], td div[class^='awsui_content'], td div[class*=' awsui_content']",
    );
    content = document.createElement("div");
    content.setAttribute(ELASTICACHE_CONTENT_ATTR, "true");
    content.className = `${referenceContent?.className || ""} maxops-ddb-cell-content`.trim();
    cell.appendChild(content);
  }

  content.replaceChildren(buildElasticacheValue(column, summary));
};

const scanElasticacheConsole = () => {
  const region = getAwsRegion();
  cachedElasticacheResources = cachedElasticacheOverview
    ? buildElasticacheResourceMap(cachedElasticacheOverview, region)
    : new Map();

  const bodyTables = findElasticacheBodyTables();
  if (bodyTables.length === 0) {
    return;
  }

  if (!isElasticacheConsolePage() && !hasElasticacheTableContext()) {
    return;
  }

  const presentationTables = findElasticachePresentationTables();
  for (const table of [...presentationTables, ...bodyTables]) {
    ensureElasticacheHeaders(table);
  }

  for (const table of bodyTables) {
    const rows = table.querySelectorAll("tbody tr");
    for (const row of rows) {
      const identifier = getRowElasticacheIdentifier(row);
      if (!identifier) {
        continue;
      }
      const summary = cachedElasticacheResources.get(identifier);
      for (const column of ELASTICACHE_COLUMNS) {
        ensureElasticacheCell(row, column, summary);
      }
    }
  }
};

const isEbsConsolePage = () => {
  const href = window.location.href.toLowerCase();
  return href.includes("/ec2/") && (href.includes("volumes") || href.includes("#volumes"));
};

const hasEbsVolumesTableContext = () =>
  Array.from(document.querySelectorAll("h1, h2, [aria-labelledby]")).some((element) =>
    normalizeText(element.textContent).toLowerCase().includes("volumes"),
  );

const getEbsVolumes = (overview) => {
  if (Array.isArray(overview?.volumes)) {
    return overview.volumes;
  }
  if (Array.isArray(overview?.resources)) {
    return overview.resources;
  }
  return [];
};

const buildEbsVolumeMap = (overview, region) => {
  const volumes = new Map();
  for (const volume of getEbsVolumes(overview)) {
    if (region && normalizeText(volume?.region) !== region) {
      continue;
    }

    const volumeId = normalizeText(volume?.resource_id);
    if (!volumeId) {
      continue;
    }

    volumes.set(volumeId, {
      volumeId,
      detailResourceId: volumeId,
      monthlyCost: getMonthlyCost(volume),
      monthlySavings: getMonthlySavings(volume),
      yearlySavings: getYearlySavings(volume),
      findings: volume?.maxops?.status && volume.maxops.status !== "healthy" ? 1 : 0,
      findingLabel: getFindingLabel(volume),
    });
  }
  return volumes;
};

const findAwsUiTables = () =>
  Array.from(
    document.querySelectorAll(
      "table[class^='awsui_table'], table[class*=' awsui_table']",
    ),
  );

const hasEbsVolumeIdHeader = (table) =>
  Array.from(table.querySelectorAll("thead th")).some(
    (cell) =>
      getAwsUiColumnId(cell) === "volume-id" ||
      normalizeText(cell.textContent).toLowerCase() === "volume id",
  );

const findEbsBodyTables = () =>
  findAwsUiTables().filter((table) =>
    table.querySelector("tbody tr") &&
    (hasEbsVolumeIdHeader(table) ||
      Array.from(table.querySelectorAll("tbody tr")).some((row) =>
        Boolean(getRowEbsVolumeId(row)),
      )),
  );

const findEbsPresentationTables = () =>
  findAwsUiTables().filter(
    (table) => !table.querySelector("tbody tr") && hasEbsVolumeIdHeader(table),
  );

const buildEbsHeaderCell = (referenceHeader, column) => {
  const header = document.createElement("th");
  header.setAttribute(EBS_HEADER_ATTR, "true");
  header.setAttribute(EBS_COLUMN_ATTR, column.key);
  header.setAttribute("scope", "col");
  header.className = `${referenceHeader?.className || ""} maxops-ddb-header-cell`.trim();
  header.style.width = column.width;

  const referenceContent = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-content']",
  );
  const content = document.createElement("div");
  content.className = `${referenceContent?.className || ""} maxops-ddb-header-content`.trim();

  const referenceText = referenceHeader?.querySelector(
    "div[class*='awsui_header-cell-text']",
  );
  const text = document.createElement("div");
  text.className = `${referenceText?.className || ""} maxops-ddb-header-text`.trim();
  text.textContent = column.label;

  content.appendChild(text);
  header.appendChild(content);
  return header;
};

const getAwsUiColumnId = (element) => {
  const analytics = element?.getAttribute("data-awsui-analytics");
  if (analytics) {
    try {
      const parsed = JSON.parse(analytics);
      return parsed?.detail?.columnId || parsed?.component?.innerContext?.columnId || null;
    } catch (_error) {
      // AWSUI analytics attributes are best-effort selectors only.
    }
  }

  const focusId = element?.getAttribute("data-focus-id") || "";
  const headerMatch = focusId.match(/^header-(.+)$/);
  if (headerMatch) {
    return headerMatch[1];
  }

  return null;
};

const findEbsVolumeIdHeader = (row) =>
  Array.from(row.children).find((cell) => getAwsUiColumnId(cell) === "volume-id") ||
  Array.from(row.children).find((cell) =>
    normalizeText(cell.textContent).toLowerCase() === "volume id",
  );

const findRowEbsVolumeIdCell = (row) =>
  Array.from(row.children).find((cell) => getAwsUiColumnId(cell) === "volume-id") ||
  Array.from(row.children).find((cell) =>
    Boolean(normalizeText(cell.textContent).match(EBS_VOLUME_ID_PATTERN)),
  );

const removeEbsColumns = (root) => {
  root
    .querySelectorAll(`[${EBS_HEADER_ATTR}="true"], [${EBS_CELL_ATTR}="true"]`)
    .forEach((element) => element.remove());
};

const ensureEbsHeaders = (table) => {
  const row = table.querySelector("thead tr");
  if (!row) {
    return;
  }

  const existingColumns = Array.from(
    row.querySelectorAll(`[${EBS_HEADER_ATTR}="true"]`),
  ).map((cell) => cell.getAttribute(EBS_COLUMN_ATTR));
  const expectedColumns = EBS_COLUMNS.map((column) => column.key);
  const volumeIdHeader = findEbsVolumeIdHeader(row);
  const firstMaxOpsHeader = row.querySelector(
    `[${EBS_HEADER_ATTR}="true"][${EBS_COLUMN_ATTR}="${expectedColumns[0]}"]`,
  );
  if (
    existingColumns.length === expectedColumns.length &&
    expectedColumns.every((column) => existingColumns.includes(column)) &&
    (!volumeIdHeader || volumeIdHeader.nextElementSibling === firstMaxOpsHeader)
  ) {
    return;
  }

  removeEbsColumns(table);
  const insertAfter = volumeIdHeader;
  const referenceHeader = Array.from(row.children)
    .reverse()
    .find((cell) => cell.tagName === "TH");
  let previousCell = insertAfter;
  for (const column of EBS_COLUMNS) {
    const headerCell = buildEbsHeaderCell(referenceHeader, column);
    if (previousCell) {
      previousCell.after(headerCell);
    } else {
      row.appendChild(headerCell);
    }
    previousCell = headerCell;
  }
};

const getRowEbsVolumeId = (row) => {
  const link = row.querySelector("a[href*='vol-']");
  const linkMatch = normalizeText(link?.textContent || link?.getAttribute("href")).match(EBS_VOLUME_ID_PATTERN);
  if (linkMatch) {
    return linkMatch[0];
  }

  const label = row.querySelector("label[aria-label]");
  const labelMatch = normalizeText(label?.getAttribute("aria-label")).match(EBS_VOLUME_ID_PATTERN);
  if (labelMatch) {
    return labelMatch[0];
  }

  const rowMatch = normalizeText(row.textContent).match(EBS_VOLUME_ID_PATTERN);
  return rowMatch ? rowMatch[0] : null;
};

const buildMaxOpsEbsResourceUrl = (summary) => {
  const resourceId = normalizeText(summary?.detailResourceId || summary?.volumeId);
  const path = resourceId
    ? `/dashboard/resources/ebs/volumes/${encodeURIComponent(resourceId)}`
    : "/dashboard/resources/ebs";
  return `${maxOpsUiBaseUrl}${path}`;
};

const getEbsColumnText = (columnKey, summary) => {
  if (!summary) {
    return "No data";
  }
  if (columnKey === "price") {
    return `${formatCurrency(summary.monthlyCost)}/mo`;
  }
  if (columnKey === "savings") {
    return `${formatCurrency(summary.yearlySavings)}/yr`;
  }
  if (summary.findings === 0) {
    return "Healthy";
  }
  return summary.findingLabel || "1 finding";
};

const buildEbsValue = (column, summary) => {
  const link = document.createElement("a");
  link.className = "maxops-ddb-link";
  link.href = buildMaxOpsEbsResourceUrl(summary);
  link.target = "_blank";
  link.rel = "noreferrer";
  link.title = summary
    ? `Open ${summary.volumeId} in MaxOps`
    : "Open EBS inventory in MaxOps";
  link.textContent = getEbsColumnText(column.key, summary);
  return link;
};

const buildEbsInlineSummary = (summary) => {
  const wrapper = document.createElement("span");
  wrapper.className = "maxops-overlay";
  wrapper.setAttribute(EBS_INLINE_ATTR, "true");

  for (const column of EBS_COLUMNS) {
    const chip = document.createElement("span");
    chip.className = "maxops-chip";
    chip.textContent = getEbsColumnText(column.key, summary);
    wrapper.appendChild(chip);
  }

  return wrapper;
};

const ensureEbsCell = (row, column, summary) => {
  let cell = row.querySelector(
    `[${EBS_CELL_ATTR}="true"][${EBS_COLUMN_ATTR}="${column.key}"]`,
  );
  if (!cell) {
    const referenceCell = Array.from(row.children)
      .reverse()
      .find((element) => element.tagName === "TD");
    cell = document.createElement("td");
    cell.setAttribute(EBS_CELL_ATTR, "true");
    cell.setAttribute(EBS_COLUMN_ATTR, column.key);
    cell.className = `${referenceCell?.className || ""} maxops-ddb-cell`.trim();
  }

  const previousMaxOpsCell =
    column.key === EBS_COLUMNS[0].key
      ? null
      : row.querySelector(
          `[${EBS_CELL_ATTR}="true"][${EBS_COLUMN_ATTR}="${
            EBS_COLUMNS[EBS_COLUMNS.findIndex((item) => item.key === column.key) - 1]?.key
          }"]`,
        );
  const insertAfter = previousMaxOpsCell || findRowEbsVolumeIdCell(row);
  if (insertAfter && insertAfter.nextSibling !== cell) {
    insertAfter.after(cell);
  } else if (!insertAfter && !cell.parentElement) {
    row.appendChild(cell);
  }

  let content = cell.querySelector(`[${EBS_CONTENT_ATTR}="true"]`);
  if (!content) {
    const referenceContent = row.querySelector(
      "td div[class*='awsui_body-cell-content'], td div[class^='awsui_content'], td div[class*=' awsui_content']",
    );
    content = document.createElement("div");
    content.setAttribute(EBS_CONTENT_ATTR, "true");
    content.className = `${referenceContent?.className || ""} maxops-ddb-cell-content`.trim();
    cell.appendChild(content);
  }

  content.replaceChildren(buildEbsValue(column, summary));
};

const ensureEbsInlineSummary = (row, summary) => {
  const volumeIdCell = findRowEbsVolumeIdCell(row);
  if (!volumeIdCell) {
    return;
  }

  volumeIdCell
    .querySelectorAll(`[${EBS_INLINE_ATTR}="true"]`)
    .forEach((element) => element.remove());

  const anchor =
    volumeIdCell.querySelector("div[class*='awsui_body-cell-content']") ||
    volumeIdCell.querySelector("a[href*='vol-']") ||
    volumeIdCell;
  anchor.appendChild(buildEbsInlineSummary(summary));
};

const scanEbsConsole = () => {
  const bodyTables = findEbsBodyTables();
  if (bodyTables.length === 0) {
    return;
  }

  if (!isEbsConsolePage() && !hasEbsVolumesTableContext()) {
    return;
  }

  const presentationTables = findEbsPresentationTables();
  const region = getAwsRegion();
  cachedEbsVolumes = cachedEbsOverview
    ? buildEbsVolumeMap(cachedEbsOverview, region)
    : new Map();

  for (const table of [...presentationTables, ...bodyTables]) {
    ensureEbsHeaders(table);
  }

  for (const table of bodyTables) {
    const rows = table.querySelectorAll("tbody tr");
    for (const row of rows) {
      const volumeId = getRowEbsVolumeId(row);
      if (!volumeId) {
        continue;
      }
      const summary = cachedEbsVolumes.get(volumeId);
      for (const column of EBS_COLUMNS) {
        ensureEbsCell(row, column, summary);
      }
      ensureEbsInlineSummary(row, summary);
    }
  }
};

const scanAwsConsole = () => {
  withMaxOpsDomUpdate(() => {
    scanEc2Console();
    scanDynamoDbConsole();
    scanEbsConsole();
    scanRdsConsole();
    scanElasticacheConsole();
  });
};

const loadEc2Overview = () => {
  safeSendMessage({ type: "MAXOPS_GET_EC2_OVERVIEW" }, (payload) => {
    cachedEc2Overview = payload;
    cachedInstances = new Map(
      (cachedEc2Overview.instances || [])
        .filter((instance) => instance.resource_id)
        .map((instance) => [instance.resource_id, instance]),
    );
    scanAwsConsole();
  });
};

const loadDynamoDbOverview = () => {
  safeSendMessage({ type: "MAXOPS_GET_DYNAMODB_OVERVIEW" }, (payload) => {
    cachedDynamoDbOverview = payload;
    scanAwsConsole();
  });
};

const loadEbsOverview = () => {
  safeSendMessage({ type: "MAXOPS_GET_EBS_OVERVIEW" }, (payload) => {
    cachedEbsOverview = payload;
    scanAwsConsole();
  });
};

const loadRdsOverview = () => {
  safeSendMessage({ type: "MAXOPS_GET_RDS_OVERVIEW" }, (payload) => {
    cachedRdsOverview = payload;
    scanAwsConsole();
  });
};

const loadElasticacheOverview = () => {
  safeSendMessage({ type: "MAXOPS_GET_ELASTICACHE_OVERVIEW" }, (payload) => {
    cachedElasticacheOverview = payload;
    scanAwsConsole();
  });
};

const loadOverview = async () => {
  try {
    await loadPluginSettings();
    if (!hasExtensionContext()) {
      return;
    }

    scanAwsConsole();
    loadEc2Overview();
    loadDynamoDbOverview();
    loadEbsOverview();
    loadRdsOverview();
    loadElasticacheOverview();
  } catch (error) {
    if (isContextInvalidatedError(error)) {
      disableExtensionContext();
    }
  }
};

const scheduleScan = () => {
  if (isApplyingMaxOpsDom) {
    return;
  }

  window.clearTimeout(scanTimer);
  scanTimer = window.setTimeout(scanAwsConsole, RESCAN_DELAY_MS);
};

const start = () => {
  void loadOverview();
  refreshTimer = window.setInterval(() => {
    void loadOverview();
  }, REFRESH_INTERVAL_MS);

  const observer = new MutationObserver(scheduleScan);
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void loadOverview();
    }
  });
};

window.addEventListener("beforeunload", () => {
  window.clearInterval(refreshTimer);
  window.clearTimeout(scanTimer);
});

start();
