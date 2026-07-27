const collectButton = document.getElementById("collect");
const stopButton = document.getElementById("stop");
const statusNode = document.getElementById("status");
const pageLimitInput = document.getElementById("page-limit");

collectButton.addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://www.linkedin.com/jobs/")) {
      throw new Error("Open a LinkedIn Jobs results page first.");
    }
    const maxPages = Math.min(15, Math.max(1, Number(pageLimitInput.value) || 12));
    pageLimitInput.value = String(maxPages);
    const result = await chrome.runtime.sendMessage({ type: "start", tabId: tab.id, maxPages });
    if (!result?.ok) throw new Error(result?.error || "Could not start collection.");
    renderStatus(result.state);
  } catch (error) {
    showStatus(error.message || String(error), "error");
  }
});

stopButton.addEventListener("click", async () => {
  const result = await chrome.runtime.sendMessage({ type: "stop" });
  renderStatus(result.state);
});

chrome.runtime.sendMessage({ type: "status" }).then((result) => renderStatus(result.state));
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "session" && changes.jobTrackerCollection) {
    renderStatus(changes.jobTrackerCollection.newValue);
  }
});

function showStatus(message, className = "") {
  statusNode.textContent = message;
  statusNode.className = className;
}

function renderStatus(state) {
  if (!state) return;
  collectButton.disabled = Boolean(state.running);
  stopButton.classList.toggle("hidden", !state.running);
  pageLimitInput.disabled = Boolean(state.running);
  const className = state.error ? "error" : state.running ? "" : "success";
  showStatus(state.message || "Ready to collect visible pages.", className);
}
