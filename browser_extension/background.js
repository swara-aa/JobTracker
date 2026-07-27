const IMPORT_URL = "http://127.0.0.1:5000/api/linkedin/import";
const MAX_PAGE_LIMIT = 15;
const PAGE_WAIT_MS = 1000;
const IMPORT_TIMEOUT_MS = 10000;
const MAX_PAGE_LOAD_RETRIES = 15;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message)
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

async function handleMessage(message) {
  if (message.type === "status") return { ok: true, state: await getState() };
  if (message.type === "stop") {
    const state = await getState();
    return { ok: true, state: await saveState({ ...state, running: false, message: "Collection stopped by you." }) };
  }
  if (message.type !== "start") return { ok: false, error: "Unknown extension action." };

  const maxPages = Math.min(MAX_PAGE_LIMIT, Math.max(1, Number(message.maxPages) || 12));
  const state = await saveState({
    running: true,
    tabId: message.tabId,
    maxPages,
    pagesVisited: 0,
    captured: 0,
    accepted: 0,
    saved: 0,
    queuedForEnrichment: 0,
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
    await pause(PAGE_WAIT_MS);
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
    await pause(PAGE_WAIT_MS);
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
  await pause(PAGE_WAIT_MS);
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
  await saveState({
    ...state,
    running: false,
    message: `${reason} Saved ${state.saved} new jobs from ${state.pagesVisited} page(s). Job Tracker is capturing public descriptions and local scores for ${state.queuedForEnrichment} new job(s) in the background.`,
  });
}

async function stopWithError(error) {
  const state = await getState();
  await saveState({ ...state, running: false, error: error.message || String(error), message: "Collection stopped." });
}

async function getState() {
  const { jobTrackerCollection } = await chrome.storage.session.get("jobTrackerCollection");
  return jobTrackerCollection || { running: false, message: "Ready to collect visible pages." };
}

async function saveState(state) {
  await chrome.storage.session.set({ jobTrackerCollection: state });
  return state;
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
