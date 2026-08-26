import "./styles.css";

const backendUrl = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
const app = document.querySelector("#app");

if (!backendUrl) {
  app.innerHTML = "<section class=\"error\"><h1>JobTracker is not configured</h1><p>This Static Web App needs a VITE_API_URL build setting.</p></section>";
} else {
  app.innerHTML = `
    <section class="error"><h1>Opening JobTracker…</h1><p>Your private workspace is opening securely.</p><p><a href="${backendUrl}">Continue to JobTracker</a></p></section>
  `;
  window.location.replace(backendUrl);
}
