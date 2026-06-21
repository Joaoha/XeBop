"use strict";

// ---- tabs ----------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll(".panel").forEach(p => { p.hidden = (p.id !== name); });
  document.querySelectorAll(".tab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  if (history.replaceState) history.replaceState(null, "", "#" + name);
}

document.addEventListener("DOMContentLoaded", () => {
  const tabs = document.querySelectorAll(".tab");
  if (tabs.length) {
    tabs.forEach(t => t.addEventListener("click", () => showTab(t.dataset.tab)));
    const initial = (location.hash || "").replace("#", "");
    showTab(document.getElementById(initial) ? initial : tabs[0].dataset.tab);
  }
  wireLocalTable();
  wireM365();
});

// ---- local employee table ------------------------------------------------
function collectEmployees() {
  const rows = document.querySelectorAll("#emp-table tbody tr");
  const out = [];
  rows.forEach(tr => {
    const get = n => (tr.querySelector(`[name=${n}]`)?.value || "").trim();
    const name = get("name");
    if (!name) return;
    out.push({
      name,
      role: get("role"),
      alt_names: get("alt_names").split(",").map(s => s.trim()).filter(Boolean),
      host_channel_id: get("host_channel_id"),
    });
  });
  return out;
}

function wireLocalTable() {
  const table = document.getElementById("emp-table");
  if (!table) return;
  const tbody = table.querySelector("tbody");

  document.getElementById("emp-add")?.addEventListener("click", () => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input name="name"></td>' +
      '<td><input name="role"></td>' +
      '<td><input name="alt_names"></td>' +
      '<td><input name="host_channel_id" placeholder="email:jo@acme.com"></td>' +
      '<td><button type="button" class="btn-link row-del">remove</button></td>';
    tbody.appendChild(tr);
  });

  tbody.addEventListener("click", e => {
    if (e.target.classList.contains("row-del")) e.target.closest("tr").remove();
  });

  document.getElementById("local-form")?.addEventListener("submit", e => {
    document.getElementById("employees_json").value = JSON.stringify(collectEmployees());
  });
}

// ---- M365 test / sync / curate ------------------------------------------
async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  return resp.json();
}

function setStatus(msg, ok) {
  const el = document.getElementById("m365-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status " + (ok ? "ok" : "err");
}

function wireM365() {
  const testBtn = document.getElementById("m365-test");
  const syncBtn = document.getElementById("m365-sync");
  if (!testBtn || !syncBtn) return;
  let candidates = [];

  testBtn.addEventListener("click", async () => {
    setStatus("Testing…", true);
    try {
      const r = await postJSON("/m365/test");
      setStatus(r.message, r.ok);
    } catch (e) { setStatus("Request failed", false); }
  });

  syncBtn.addEventListener("click", async () => {
    setStatus("Syncing…", true);
    try {
      const r = await postJSON("/m365/sync");
      if (!r.ok) { setStatus(r.message || "Sync failed", false); return; }
      candidates = r.candidates || [];
      renderCandidates(candidates);
      setStatus(`Fetched ${candidates.length} people. Tick who should be greetable.`, true);
    } catch (e) { setStatus("Request failed", false); }
  });

  document.getElementById("m365-curate-form")?.addEventListener("submit", async e => {
    e.preventDefault();
    const chosen = [];
    document.querySelectorAll("#m365-candidates input:checked").forEach(cb => {
      const c = candidates[parseInt(cb.value, 10)];
      if (c) chosen.push(c);
    });
    const r = await postJSON("/m365/curate", { employees: chosen });
    if (r.ok) {
      setStatus(`Saved ${r.count} greetable people. Restart the agent to apply.`, true);
      renderCollisions(r.collisions || []);
    } else {
      setStatus(r.message || "Save failed", false);
    }
  });
}

function renderCandidates(list) {
  const box = document.getElementById("m365-candidates");
  const form = document.getElementById("m365-curate-form");
  box.innerHTML = "";
  list.forEach((c, i) => {
    const label = document.createElement("label");
    label.innerHTML =
      `<input type="checkbox" value="${i}"> ${escapeHtml(c.name)}` +
      (c.role ? ` — <span class="muted">${escapeHtml(c.role)}</span>` : "") +
      ` <span class="muted">[${escapeHtml(c.host_channel_id)}]</span>`;
    box.appendChild(label);
  });
  form.hidden = list.length === 0;
}

function renderCollisions(collisions) {
  const box = document.getElementById("m365-collisions");
  if (!collisions.length) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = "<strong>Name collisions — these people may not be routed reliably:</strong><br>" +
    collisions.map(c => `“${escapeHtml(c.token)}” → ${c.names.map(escapeHtml).join(", ")}`).join("<br>");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
