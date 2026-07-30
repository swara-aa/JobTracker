const IMPORT_URL = "http://127.0.0.1:5000/api/linkedin/import?defer_enrichment=1";
const FINALIZE_URL = "http://127.0.0.1:5000/api/linkedin/finalize-collection";
const MAX_PAGE_LIMIT = 15;
const PAGE_LOAD_RETRY_MS = 1000;
const NEXT_PAGE_WAIT_MS = 10000;
const IMPORT_TIMEOUT_MS = 10000;
const MAX_PAGE_LOAD_RETRIES = 15;
const SCHEDULE_ALARM = "jobTrackerDailyCollection";
const RECOVERY_ALARM = "jobTrackerMissedRunRecovery";
const RECOVERY_INTERVAL_MINUTES = 5;
const MAX_DAILY_ATTEMPTS = 3;
const SCHEDULE_KEY = "jobTrackerSchedule";
const DEFAULT_SCHEDULE = {
  enabled: false,
  time: "08:00",
  maxPages: 12,
  searchUrl: "",
  lastAttemptDate: "",
  lastAttemptAt: "",
  attemptCount: 0,
  lastRunDate: "",
  lastResult: "Daily collection is not enabled.",
  warning: "",
};

chrome.runtime.onInstalled.addListener(() => {
  ensureSchedule().then(ensureScheduleAlarms);
});

chrome.runtime.onStartup.addListener(() => {
  ensureSchedule().then(async (schedule) => {
    try {
      await maybeRunMissedSchedule(schedule);
    } catch (error) {
      await recordScheduleResult(`Missed scheduled collection failed: ${error.message || String(error)}`);
    }
    await ensureScheduleAlarms(await getSchedule());
  });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === RECOVERY_ALARM) {
    ensureSchedule()
      .then(maybeRunMissedSchedule)
      .catch((error) => recordScheduleResult(`Missed-run recovery failed: ${error.message || String(error)}`));
    return;
  }
  if (alarm.name === SCHEDULE_ALARM) {
    runScheduledCollection(false)
      .catch((error) => recordScheduleResult(`Scheduled collection failed: ${error.message || String(error)}`))
      .finally(async () => scheduleNextAlarm(await getSchedule()));
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

async function handleMessage(message) {
  if (message.type === "status") {
    return { ok: true, state: await getState(), schedule: await ensureSchedule() };
  }
  if (message.type === "saveSchedule") {
    const schedule = await saveSchedule(message.schedule || {});
    await ensureScheduleAlarms(schedule);
    return { ok: true, schedule };
  }
  if (message.type === "runScheduledNow") {
    const state = await runScheduledCollection(true);
    return { ok: true, state, schedule: await getSchedule() };
  }
  if (message.type === "stop") {
    const state = await getState();
    return { ok: true, state: await saveState({ ...state, running: false, message: "Collection stopped by you." }) };
  }
  if (message.type !== "start") return { ok: false, error: "Unknown extension action." };

  return startCollection(message.tabId, message.maxPages, "manual");
}

async function startCollection(tabId, requestedMaxPages, trigger) {
  const currentState = await getState();
  if (currentState.running) {
    throw new Error("A collection is already running.");
  }
  const maxPages = Math.min(MAX_PAGE_LIMIT, Math.max(1, Number(requestedMaxPages) || 12));
  const state = await saveState({
    running: true,
    tabId,
    trigger,
    maxPages,
    pagesVisited: 0,
    captured: 0,
    accepted: 0,
    saved: 0,
    queuedForEnrichment: 0,
    newLinks: [],
    lastSignature: "",
    retries: 0,
    error: "",
    message: "Reading the first visible results page...",
  });
  try {
    await processPage();
    return { ok: true, state: await getState() };
  } catch (error) {
    await stopWithError(error);
    return { ok: false, error: error.message || String(error), state: await getState() };
  }
}

async function processPage() {
  const state = await getState();
  if (!state.running) return;

  const tab = await chrome.tabs.get(state.tabId).catch(() => null);
  if (!tab?.url?.startsWith("https://www.linkedin.com/jobs/")) {
    throw new Error("Collection stopped because the LinkedIn Jobs tab was closed or changed.");
  }
  const [{ result: page }] = await chrome.scripting.executeScript({
    target: { tabId: state.tabId },
    func: readVisibleJobsPage,
  });
  if (!page?.jobs?.length) {
    if (state.retries >= MAX_PAGE_LOAD_RETRIES) {
      throw new Error("No visible job cards loaded after 15 seconds. Keep the results list open and try again.");
    }
    await saveState({
      ...state,
      retries: state.retries + 1,
      message: `Waiting for visible job cards to load (${state.retries + 1}/${MAX_PAGE_LOAD_RETRIES})...`,
    });
    await pause(PAGE_LOAD_RETRY_MS);
    return processPage();
  }
  if (page.signature === state.lastSignature) {
    if (state.retries >= MAX_PAGE_LOAD_RETRIES) {
      throw new Error("The next results page did not load after 15 seconds. Collection stopped safely.");
    }
    await saveState({
      ...state,
      retries: state.retries + 1,
      message: `Waiting for the next results page to load (${state.retries + 1}/${MAX_PAGE_LOAD_RETRIES})...`,
    });
    await pause(PAGE_LOAD_RETRY_MS);
    return processPage();
  }

  const savingState = await saveState({
    ...state,
    message: `Found ${page.jobs.length} visible job card(s); saving them to Job Tracker...`,
  });
  const response = await fetchWithTimeout(IMPORT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(page.jobs),
  });
  const imported = await response.json();
  if (!response.ok) throw new Error(imported.error || "The local Job Tracker import failed.");

  const updated = await saveState({
    ...savingState,
    pagesVisited: savingState.pagesVisited + 1,
    captured: savingState.captured + imported.captured,
    accepted: savingState.accepted + imported.accepted,
    saved: savingState.saved + imported.saved,
    queuedForEnrichment: savingState.queuedForEnrichment + (imported.queued_for_enrichment || 0),
    newLinks: [...new Set([...(savingState.newLinks || []), ...(imported.new_links || [])])],
    lastSignature: page.signature,
    retries: 0,
    message: `Page ${savingState.pagesVisited + 1}: saved ${imported.saved} new job(s).`,
  });
  if (updated.pagesVisited >= updated.maxPages || !page.hasNext) {
    await finish(updated, page.hasNext ? "Reached your page limit." : "Reached the last visible results page.");
    return;
  }

  const [{ result: moved }] = await chrome.scripting.executeScript({
    target: { tabId: updated.tabId },
    func: clickNextResultsPage,
  });
  if (!moved) {
    await finish(updated, "Could not find an enabled Next button. Collection is complete.");
    return;
  }
  await saveState({ ...updated, message: `Saved page ${updated.pagesVisited}; opening the next page...` });
  await pause(NEXT_PAGE_WAIT_MS);
  return processPage();
}

async function fetchWithTimeout(url, options) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), IMPORT_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("Job Tracker did not respond within 10 seconds. Make sure Flask is running at http://127.0.0.1:5000.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function pause(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function finish(state, reason) {
  let postprocessingMessage = "No new jobs need post-processing.";
  try {
    const response = await fetchWithTimeout(FINALIZE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        links: state.newLinks || [],
        pagesVisited: state.pagesVisited,
        captured: state.captured,
        saved: state.saved,
      }),
    });
    const finalized = await response.json();
    if (!response.ok) throw new Error(finalized.error || "Could not schedule post-processing.");
    postprocessingMessage = finalized.message || "Automatic post-processing is scheduled.";
  } catch (error) {
    postprocessingMessage = `Jobs were saved, but automatic post-processing could not be scheduled: ${error.message || String(error)}`;
  }
  await saveState({
    ...state,
    running: false,
    message: `${reason} Saved ${state.saved} new jobs from ${state.pagesVisited} page(s). ${postprocessingMessage}`,
  });
  if (state.trigger === "scheduled") {
    const today = localDateKey();
    const schedule = await getSchedule();
    await chrome.storage.local.set({
      [SCHEDULE_KEY]: {
        ...schedule,
        lastRunDate: today,
        lastResult: `Completed: saved ${state.saved} new jobs from ${state.pagesVisited} page(s).`,
      },
    });
  }
}

async function stopWithError(error) {
  const state = await getState();
  await saveState({ ...state, running: false, error: error.message || String(error), message: "Collection stopped." });
  if (state.trigger === "scheduled") {
    await recordScheduleResult(`Scheduled collection stopped: ${error.message || String(error)}`);
  }
}

async function getState() {
  const { jobTrackerCollection } = await chrome.storage.session.get("jobTrackerCollection");
  return jobTrackerCollection || { running: false, message: "Ready to collect visible pages." };
}

async function saveState(state) {
  await chrome.storage.session.set({ jobTrackerCollection: state });
  return state;
}

async function ensureSchedule() {
  const stored = await getSchedule();
  if (stored.searchUrl || stored.enabled || stored.lastResult !== DEFAULT_SCHEDULE.lastResult) {
    return stored;
  }
  await chrome.storage.local.set({ [SCHEDULE_KEY]: stored });
  return stored;
}

async function getSchedule() {
  const stored = await chrome.storage.local.get(SCHEDULE_KEY);
  return { ...DEFAULT_SCHEDULE, ...(stored[SCHEDULE_KEY] || {}) };
}

async function saveSchedule(input) {
  const time = /^\d{2}:\d{2}$/.test(String(input.time || "")) ? String(input.time) : "08:00";
  const [hour, minute] = time.split(":").map(Number);
  if (hour > 23 || minute > 59) throw new Error("Choose a valid daily collection time.");
  const searchUrl = normalizeSearchUrl(String(input.searchUrl || "").trim());
  if (input.enabled && !searchUrl.startsWith("https://www.linkedin.com/jobs/")) {
    throw new Error("Save a LinkedIn Jobs search URL before enabling the schedule.");
  }
  const parsedUrl = searchUrl ? new URL(searchUrl) : null;
  const warning =
    input.enabled && parsedUrl?.searchParams.get("f_TPR") !== "r86400"
      ? "The saved search does not appear to use LinkedIn's Past 24 hours filter."
      : "";
  const existing = await getSchedule();
  const schedule = {
    ...existing,
    enabled: Boolean(input.enabled),
    time,
    maxPages: Math.min(MAX_PAGE_LIMIT, Math.max(1, Number(input.maxPages) || 12)),
    searchUrl,
    warning,
    lastResult: input.enabled ? existing.lastResult : "Daily collection is not enabled.",
  };
  await chrome.storage.local.set({ [SCHEDULE_KEY]: schedule });
  return schedule;
}

async function scheduleNextAlarm(schedule) {
  await chrome.alarms.clear(SCHEDULE_ALARM);
  if (!schedule.enabled) return;
  await chrome.alarms.create(SCHEDULE_ALARM, { when: nextScheduledTime(schedule.time) });
}

async function ensureScheduleAlarms(schedule) {
  await scheduleNextAlarm(schedule);
  const recoveryAlarm = await chrome.alarms.get(RECOVERY_ALARM);
  if (!recoveryAlarm) {
    await chrome.alarms.create(RECOVERY_ALARM, {
      delayInMinutes: 1,
      periodInMinutes: RECOVERY_INTERVAL_MINUTES,
    });
  }
}

async function maybeRunMissedSchedule(schedule) {
  if (!schedule.enabled) return;
  const today = localDateKey();
  if (schedule.lastRunDate === today) return;
  if (
    schedule.lastAttemptDate === today &&
    Number(schedule.attemptCount || 0) >= MAX_DAILY_ATTEMPTS
  ) {
    return;
  }
  const [hour, minute] = schedule.time.split(":").map(Number);
  const now = new Date();
  if (now.getHours() * 60 + now.getMinutes() < hour * 60 + minute) return;
  await runScheduledCollection(false);
}

async function runScheduledCollection(force) {
  const schedule = await getSchedule();
  if (!schedule.enabled && !force) throw new Error("Daily collection is not enabled.");
  if (!schedule.searchUrl.startsWith("https://www.linkedin.com/jobs/")) {
    throw new Error("The scheduled LinkedIn Jobs URL is missing.");
  }
  const today = localDateKey();
  if (!force && schedule.lastRunDate === today) {
    return getState();
  }
  const attemptCount =
    schedule.lastAttemptDate === today ? Number(schedule.attemptCount || 0) + 1 : 1;
  if (!force && attemptCount > MAX_DAILY_ATTEMPTS) return getState();
  await chrome.storage.local.set({
    [SCHEDULE_KEY]: {
      ...schedule,
      lastAttemptDate: today,
      lastAttemptAt: new Date().toISOString(),
      attemptCount,
      lastResult: "Opening the saved LinkedIn search...",
    },
  });
  const tab = await chrome.tabs.create({ url: schedule.searchUrl, active: true });
  await waitForTabReady(tab.id);
  await pause(4000);
  const result = await startCollection(tab.id, schedule.maxPages, "scheduled");
  if (!result.ok) throw new Error(result.error || "Scheduled collection failed.");
  return result.state;
}

async function recordScheduleResult(message) {
  const schedule = await getSchedule();
  await chrome.storage.local.set({ [SCHEDULE_KEY]: { ...schedule, lastResult: message } });
}

function nextScheduledTime(time) {
  const [hour, minute] = time.split(":").map(Number);
  const next = new Date();
  next.setHours(hour, minute, 0, 0);
  if (next.getTime() <= Date.now()) next.setDate(next.getDate() + 1);
  return next.getTime();
}

function localDateKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function normalizeSearchUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value);
    for (const name of ["currentJobId", "start", "position", "pageNum"]) {
      url.searchParams.delete(name);
    }
    return url.toString();
  } catch {
    return value;
  }
}

function waitForTabReady(tabId) {
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, 30000);
    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId !== tabId || changeInfo.status !== "complete") return;
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function readVisibleJobsPage() {
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
  const findNext = () => {
    const currentPage = document.querySelector('button[aria-current="true"][aria-label^="Page "]');
    const currentNumber = Number(currentPage?.getAttribute("aria-label")?.replace("Page ", ""));
    const numberedNext = Number.isInteger(currentNumber)
      ? document.querySelector(`button[aria-label="Page ${currentNumber + 1}"]`)
      : null;
    if (numberedNext && !numberedNext.disabled && numberedNext.getAttribute("aria-disabled") !== "true") {
      return numberedNext;
    }
    const candidates = [
      ...document.querySelectorAll('[data-testid="pagination-controls-next-button-visible"], [data-testid^="pagination-controls-next-button"], button[aria-label*="Next" i], a[aria-label*="Next" i], [data-test-pagination-page-btn="next"]'),
    ];
    return candidates.find((element) => !element.disabled && element.getAttribute("aria-disabled") !== "true") || null;
  };
  const cardSelector = "[role='button'][componentkey^='job-card-component-ref-'], [componentkey^='job-card-component-ref-']";
  const seenLinks = new Set();
  const jobs = [...document.querySelectorAll(cardSelector)]
    .map((card) => {
      const jobId = card.getAttribute("componentkey")?.match(/(\d+)$/)?.[1];
      const lines = (card.innerText || "").split("\n").map(clean).filter((line) => line && line !== "·");
      if (!jobId || lines.length < 2) return null;
      return {
        title: lines[0],
        company: lines[1],
        location: lines.slice(2).find((line) => /United States|Remote|Hybrid|On-site|,\s*[A-Z]{2}\b/i.test(line)) || "Unknown location",
        posting_date_text: lines.slice(2).find((line) => /(?:minute|hour|day|week)s? ago|today|just now/i.test(line)) || "",
        link: `https://www.linkedin.com/jobs/view/${jobId}`,
        search_url: window.location.href,
        role_query: "",
      };
    })
    .filter((job) => job && !seenLinks.has(job.link) && seenLinks.add(job.link))
    .slice(0, 100);
  return {
    jobs,
    signature: jobs.map((job) => job.link).join("|"),
    hasNext: Boolean(findNext()),
  };
}

function clickNextResultsPage() {
  const currentPage = document.querySelector('button[aria-current="true"][aria-label^="Page "]');
  const currentNumber = Number(currentPage?.getAttribute("aria-label")?.replace("Page ", ""));
  const numberedNext = Number.isInteger(currentNumber)
    ? document.querySelector(`button[aria-label="Page ${currentNumber + 1}"]`)
    : null;
  if (numberedNext && !numberedNext.disabled && numberedNext.getAttribute("aria-disabled") !== "true") {
    numberedNext.click();
    return true;
  }
  const candidates = [
    ...document.querySelectorAll('[data-testid="pagination-controls-next-button-visible"], [data-testid^="pagination-controls-next-button"], button[aria-label*="Next" i], a[aria-label*="Next" i], [data-test-pagination-page-btn="next"]'),
  ];
  const button = candidates.find((element) => !element.disabled && element.getAttribute("aria-disabled") !== "true");
  if (!button) return false;
  button.click();
  return true;
}
