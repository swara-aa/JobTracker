const collectButton = document.getElementById("collect");
const stopButton = document.getElementById("stop");
const statusNode = document.getElementById("status");
const pageLimitInput = document.getElementById("page-limit");
const scheduleEnabledInput = document.getElementById("schedule-enabled");
const scheduleTimeInput = document.getElementById("schedule-time");
const searchUrlInput = document.getElementById("search-url");
const useCurrentUrlButton = document.getElementById("use-current-url");
const saveScheduleButton = document.getElementById("save-schedule");
const runScheduledNowButton = document.getElementById("run-scheduled-now");
const scheduleStatusNode = document.getElementById("schedule-status");

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

useCurrentUrlButton.addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url?.startsWith("https://www.linkedin.com/jobs/")) {
    showScheduleStatus("Open the LinkedIn Jobs search you want to run every morning.", "error");
    return;
  }
  searchUrlInput.value = tab.url;
  showScheduleStatus("Current LinkedIn search selected.");
});

saveScheduleButton.addEventListener("click", async () => {
  try {
    const result = await chrome.runtime.sendMessage({
      type: "saveSchedule",
      schedule: {
        enabled: scheduleEnabledInput.checked,
        time: scheduleTimeInput.value,
        maxPages: pageLimitInput.value,
        searchUrl: searchUrlInput.value,
      },
    });
    if (!result?.ok) throw new Error(result?.error || "Could not save the daily schedule.");
    renderSchedule(result.schedule);
    showScheduleStatus(
      result.schedule.warning || (result.schedule.enabled ? "Daily schedule saved." : "Daily collection is disabled."),
      result.schedule.warning ? "error" : "success",
    );
  } catch (error) {
    showScheduleStatus(error.message || String(error), "error");
  }
});

runScheduledNowButton.addEventListener("click", async () => {
  try {
    runScheduledNowButton.disabled = true;
    const result = await chrome.runtime.sendMessage({ type: "runScheduledNow" });
    if (!result?.ok) throw new Error(result?.error || "Could not start the scheduled test.");
    renderStatus(result.state);
    renderSchedule(result.schedule);
  } catch (error) {
    showScheduleStatus(error.message || String(error), "error");
  } finally {
    runScheduledNowButton.disabled = false;
  }
});

chrome.runtime.sendMessage({ type: "status" }).then((result) => {
  renderStatus(result.state);
  renderSchedule(result.schedule);
});
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName === "session" && changes.jobTrackerCollection) {
    renderStatus(changes.jobTrackerCollection.newValue);
  }
  if (areaName === "local" && changes.jobTrackerSchedule) {
    renderSchedule(changes.jobTrackerSchedule.newValue);
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

function showScheduleStatus(message, className = "") {
  scheduleStatusNode.textContent = message;
  scheduleStatusNode.className = `schedule-status ${className}`.trim();
}

function renderSchedule(schedule) {
  if (!schedule) return;
  scheduleEnabledInput.checked = Boolean(schedule.enabled);
  scheduleTimeInput.value = schedule.time || "08:00";
  searchUrlInput.value = schedule.searchUrl || "";
  pageLimitInput.value = String(schedule.maxPages || pageLimitInput.value || 12);
  showScheduleStatus(schedule.warning || schedule.lastResult || "Daily collection is not enabled.", schedule.warning ? "error" : "");
}
