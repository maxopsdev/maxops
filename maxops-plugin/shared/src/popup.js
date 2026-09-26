const extensionApi = globalThis.maxopsBrowser || chrome;

const formatCurrency = (value) => {
  const amount = Number(value || 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: amount >= 100 ? 0 : 2,
  }).format(amount);
};

const setText = (id, value) => {
  document.getElementById(id).textContent = value;
};

extensionApi.runtime.sendMessage({ type: "MAXOPS_GET_EC2_OVERVIEW" }, (response) => {
  const status = document.getElementById("status");
  const message = document.getElementById("message");

  if (extensionApi.runtime.lastError || !response?.ok) {
    status.textContent = "Offline";
    status.className = "status status--error";
    message.textContent =
      extensionApi.runtime.lastError?.message ||
      response?.error ||
      "Unable to reach the local MaxOps backend.";
    return;
  }

  const summary = response.payload.summary || {};
  status.textContent = "Connected";
  status.className = "status status--ok";
  setText("instances", summary.total_instances ?? 0);
  setText("actionable", summary.actionable_instances ?? 0);
  setText("monthlyCost", formatCurrency(summary.monthly_cost_estimate));
  setText("yearlySavings", formatCurrency(summary.potential_savings_yearly));
  message.textContent = response.payload.generated_at
    ? `Inventory generated at ${new Date(response.payload.generated_at).toLocaleString()}`
    : "No generated timestamp available.";
});
