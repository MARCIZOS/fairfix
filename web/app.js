/* FairFix frontend. Plain JS, no build step.
   Bill data lives in this page's memory only; nothing is saved to the browser. */
"use strict";

const S = { vehicles: [], jobs: [], jobsById: {}, config: {}, bill: null, estimate: null, flags: [],
            report: null, readTrace: null, lang: "en", demo: false, vin: "" };

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "html") n.innerHTML = v;            // only ever used with constant SVG strings below
    else if (k === "style") n.setAttribute("style", v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
}

const ICONS = {
  tick: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 11 L8 15.5 L17 4"/></svg>',
  ask: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 7.2 C7 4.4 13 4.4 13 7.4 C13 9.6 10 9.8 10 12.2"/><path d="M10 15.6 L10 15.8"/></svg>',
  dash: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 10 L15 10"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 8 L10 13 L15 8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  dot: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10.5 L8.5 15 L16 5"/></svg>',
  pen: '<svg class="pen-svg" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true"><path pathLength="1" d="M2 7 C 25 3, 55 11, 98 5"/></svg>',
};

const inr = (x) => (x === null || x === undefined || x === "") ? "?" :
  "₹" + Number(x).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: Number(x) % 1 ? 2 : 0 });

function num(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/[^\d.\-]/g, "");
  if (s === "" || s === "-" || s === ".") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/* Accounts: the session is an HttpOnly cookie; auth-ui.js renders the sign-in card. */
const AUTH = { required: true, signedIn: false };

function onSession(sess) {
  AUTH.required = sess.required; AUTH.signedIn = sess.signed_in;
  const inSession = AUTH.required && AUTH.signedIn;
  $("#signed").hidden = !inSession;
  $("#signed").title = inSession ? `Signed in as ${sess.email}` : "";
  $("#nav-signin").hidden = inSession;
}

/* Confirm the session before opening a private bill workflow. */
async function ensureAccess() {
  const session = await FFAuth.refresh();
  if (!session.required || session.signed_in) return true;
  location.href = "/signin.html";
  return false;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  if (r.status === 401) {                    // session ended (signed out elsewhere, expired)
    await FFAuth.refresh();
    ensureAccess();
    throw new Error("Your session ended. Sign in again to continue.");
  }
  let body = null;
  try { body = await r.json(); } catch (_) { /* empty */ }
  if (!r.ok) {
    const msg = (body && (body.detail || body.error)) || `Request failed (${r.status})`;
    throw new Error(typeof msg === "string" ? msg : "Request failed");
  }
  return body;
}

function toast(msg) {
  let t = $("#toast");
  if (!t) { t = el("div", { id: "toast", class: "toast", role: "alert" }); document.body.append(t); }
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._h); toast._h = setTimeout(() => t.classList.remove("show"), 6000);
}

function busy(on, text = "Working") {
  $("#busy").hidden = !on;
  $("#busy-text").textContent = text;
  $$("button.primary").forEach((b) => (b.disabled = on));
}

function show(view) {
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  $("#app").className = view === "home" ? "home" : view === "result" ? "results" : "";
  window.scrollTo({ top: 0, behavior: REDUCED ? "auto" : "smooth" });
  const h = $(`#view-${view} h1, #view-${view} h2, #view-${view} strong[id]`);
  if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
}

/* ------------------------------------------------------------------ home */

/* Hero: one orchestrated sequence. The job card prints, Nemotron's scan bar reads it
   (personal details get blacked out), the rules mark each line, then the question is typed. */
const HERO_ICON = {
  ok: '<svg viewBox="0 0 20 20"><path pathLength="1" d="M3 11 L8 15.5 L17 4"/></svg>',
  ask: '<svg viewBox="0 0 20 20"><path pathLength="1" d="M6.5 6.8 C6.5 3.6 13.5 3.6 13.5 7 C13.5 9.6 10 9.8 10 12.6"/><path pathLength="1" d="M10 16.2 L10 16.6"/></svg>',
  unk: '<svg viewBox="0 0 20 20"><path pathLength="1" d="M5 10 L15 10"/></svg>',
};
const hero = { timers: [] };

function heroInit() {
  const fig = $("#hero-bill");
  if (!fig) return;
  $$(".jc-tr[data-v]", fig).forEach((r) => ($(".v", r).innerHTML = HERO_ICON[r.dataset.v]));
  $("#hero-replay").addEventListener("click", () => heroPlay(true));
  if (REDUCED) { heroFinal(); return; }
  // Hidden pages pause CSS animations but not timers, so only play while the page is visible,
  // and if the visitor leaves mid-sequence, show the finished card when they come back.
  const start = () => (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => heroPlay(false));
  if (document.hidden) document.addEventListener("visibilitychange", function once() {
    if (!document.hidden) { document.removeEventListener("visibilitychange", once); start(); }
  });
  else start();
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && hero.playing) { hero.timers.forEach(clearTimeout); hero.timers = []; hero.playing = false; hero.cut = true; }
    else if (!document.hidden && hero.cut) { hero.cut = false; heroFinal(); }
  });
}

function heroSteps(phase) {
  const order = ["read", "check", "ask"];
  const at = order.indexOf(phase);
  $$("#hero-steps li").forEach((li, i) => {
    li.classList.toggle("on", i === at);
    li.classList.toggle("done", phase === "done" || i < at);
  });
}

function heroFinal() {
  const fig = $("#hero-bill");
  fig.className = "jobcard-wrap static";
  $(".wa-text", fig).textContent = $(".wa-text", fig).dataset.text;
  heroSteps("done");
}

function heroPlay(again) {
  const fig = $("#hero-bill");
  hero.timers.forEach(clearTimeout); hero.timers = [];
  if (REDUCED) { heroFinal(); return; }
  const text = $(".wa-text", fig);
  text.textContent = "";
  fig.className = "jobcard-wrap";
  void fig.offsetWidth;                      // restart CSS animations
  const at = (ms, fn) => hero.timers.push(setTimeout(fn, ms));
  hero.playing = true; hero.cut = false;
  fig.classList.add("play"); heroSteps("read");
  at(1700, () => fig.classList.add("ph-scan"));
  at(3000, () => { fig.classList.add("ph-check"); heroSteps("check"); });
  at(5600, () => { fig.classList.add("ph-ask"); heroSteps("ask"); });
  const full = text.dataset.text;
  at(6200, () => {
    let i = 0;
    const tick = () => { text.textContent = full.slice(0, ++i); if (i < full.length) hero.timers.push(setTimeout(tick, 22)); else at(900, () => { heroSteps("done"); hero.playing = false; }); };
    tick();
  });
  if (again) fig.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function init() {
  document.addEventListener("click", async (e) => {
    const go = e.target.closest("[data-go]");
    if (!go) return;
    e.preventDefault();
    // Signed-in people go straight to the bill form; everyone else signs in first.
    if (go.dataset.go === "vehicle" && !(await ensureAccess())) return;
    show(go.dataset.go);
  });
  $("#signed").addEventListener("click", async () => {
    await FFAuth.signOut();
    show("home");
    $("#home-signin").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
  });
  heroInit();
  FFAuth.mount($("#hs-card"), {
    checkButton: () => el("button", { type: "button", class: "btn primary", "data-go": "vehicle" }, "Check a bill"),
  });
  FFAuth.onChange(onSession);
  try {
    const [cfg, vehicles, jobs, demos] = await Promise.all([
      api("/api/config"), api("/api/vehicles"), api("/api/jobs"), api("/api/demo"), FFAuth.refresh()]);
    S.config = cfg; S.vehicles = vehicles; S.jobs = jobs;
    jobs.forEach((j) => (S.jobsById[j.id] = j));
    if (cfg.engines) $("#scan-engine").textContent = cfg.engines.vision;
    $("#privacy-note").textContent = cfg.privacy +
      ` Prices are flagged only when more than ${cfg.price_review_threshold_pct}% above the highest of at least ` +
      `${cfg.price_min_comparables} equivalent listings from ${cfg.price_min_sellers} or more sellers.`;
    renderDemos(demos);
    renderVehicleSelect();
    if (location.hash === "#check") {          // arriving from the sign-in page
      history.replaceState(null, "", "/");
      if (await ensureAccess()) show("vehicle");
    }
  } catch (err) {
    $("#demo-list").append(el("p", { class: "error", text: `Couldn't reach the FairFix server: ${err.message}` }));
  }
  bindVehicleForm();
  bindCorrection();
  bindResult();
}

function renderDemos(demos) {
  const box = $("#demo-list");
  box.replaceChildren();
  if (!demos.length) return;
  box.append(el("h2", { text: "Or try a sample bill" }),
             el("p", { class: "hint", text: "The vehicle and its reference facts are fictional, but the checks are the real ones." }));
  demos.forEach((d) => box.append(
    el("button", { class: "demo-item", onclick: () => runDemo(d.name) },
      el("span", { class: "ico", "aria-hidden": "true" }), el("strong", { text: d.title }), el("span", { class: "d", text: d.description }))));
}

async function runDemo(name) {
  busy(true, "Running the sample through the checks");
  try {
    const out = await api(`/api/demo/${encodeURIComponent(name)}`);
    S.demo = true; S.lang = "en"; S.readTrace = null; S.bill = null;
    S.report = out.report;
    show("result");
    renderResult();
  } catch (err) {
    toast(err.message);
  } finally { busy(false); }
}

/* ------------------------------------------------------------------ vehicle */

function renderVehicleSelect() {
  const sel = $("#vehicle-select");
  sel.replaceChildren(el("option", { value: "", text: "Choose your vehicle" }));
  S.vehicles.forEach((v) => {
    const tag = v.fictional ? " (sample, fictional)" : v.supported ? "" : " (partial check: not yet supported)";
    sel.append(el("option", { value: v.vehicle_id, text: v.display_name + tag }));
  });
  sel.append(el("option", { value: "__other", text: "Another vehicle (partial check)" }));
  sel.addEventListener("change", () => {
    const v = S.vehicles.find((x) => x.vehicle_id === sel.value);
    $$(".other-only").forEach((n) => (n.hidden = sel.value !== "__other"));
    $("#vehicle-support").textContent = !sel.value ? "" :
      v && v.supported ? (v.fictional ? "Sample vehicle with fictional reference facts, for trying the app." :
        "Supported: schedule, price, warranty and recall checks use hand-checked official sources.") :
      "Not yet supported: only the arithmetic and authorization checks can run. The rest will show Unable to verify.";
  });
}

function approvalRow(a = {}) {
  const row = el("div", { class: "approval" },
    el("div", { class: "row" },
      el("label", {}, "What you approved", el("input", { "data-a": "item", value: a.item || "", placeholder: "e.g. rear brake shoes" })),
      el("label", {}, "Amount (if agreed)", el("input", { "data-a": "amount_inr", inputmode: "decimal", value: a.amount_inr ?? "" }))),
    el("div", { class: "row" },
      el("label", {}, "When", el("input", { type: "date", "data-a": "on", value: a.on || "" })),
      el("label", {}, "How", el("input", { "data-a": "how", value: a.how || "", placeholder: "e.g. phone call with advisor" }))),
    el("button", { type: "button", class: "btn quiet small", onclick: () => row.remove() }, "Remove note"));
  $("#approvals").append(row);
}

function readVehicle() {
  const f = $("#vehicle-form");
  const g = (n) => (f.elements[n] && f.elements[n].value.trim()) || null;
  const id = g("vehicle_id");
  const pack = S.vehicles.find((v) => v.vehicle_id === id);
  const v = {
    vehicle_id: id === "__other" ? null : id,
    make: pack ? pack.make : (g("make") || "Unknown"),
    model: pack ? pack.model : (g("model") || "Unknown"),
    variant: g("variant"), fuel: g("fuel"), odometer_km: num(g("odometer_km")), city: g("city"),
    purchase_date: g("purchase_date"), registration_date: g("registration_date"), mfg_month: g("mfg_month"),
    severe_use: f.elements.severe_use.checked, history: [],
  };
  S.vin = $("#vin").value.trim();   // kept in this page only
  return v;
}

function readApprovals() {
  return $$(".approval").map((row) => {
    const g = (k) => $(`[data-a="${k}"]`, row).value.trim();
    return { item: g("item"), amount_inr: num(g("amount_inr")), on: g("on") || null, how: g("how") };
  }).filter((a) => a.item);
}

function bindThumbs(inputId, dropId) {
  const input = $(inputId), drop = $(dropId);
  input.addEventListener("change", () => {
    const box = $(".thumbs", drop);
    box.replaceChildren();
    [...input.files].slice(0, 6).forEach((f) => box.append(el("img", { src: URL.createObjectURL(f), alt: "" })));
    drop.classList.toggle("has-file", input.files.length > 0);
  });
}

function bindVehicleForm() {
  $("#walkthrough-btn").addEventListener("click", async () => {
    try {
      const response = await fetch("/samples/manual-walkthrough.json");
      if (!response.ok) throw new Error("Sample could not be loaded");
      S.walkthrough = await response.json();
      for (const [inputId, filename] of [["#bill-files", "walkthrough-bill.png"], ["#estimate-files", "walkthrough-estimate.png"]]) {
        const photo = await fetch(`/samples/${filename}`);
        if (!photo.ok) throw new Error("Sample photo could not be loaded");
        const transfer = new DataTransfer();
        transfer.items.add(new File([await photo.blob()], filename, { type: "image/png" }));
        $(inputId).files = transfer.files;
        $(inputId).dispatchEvent(new Event("change"));
      }
      const form = $("#vehicle-form");
      for (const [key, value] of Object.entries(S.walkthrough.vehicle)) {
        if (form.elements[key] && value !== null && typeof value !== "object") form.elements[key].value = value;
      }
      $("#vehicle-select").dispatchEvent(new Event("change"));
      $("#approvals").replaceChildren();
      for (const approval of S.walkthrough.approvals || []) {
        approvalRow();
        const row = $("#approvals").lastElementChild;
        for (const input of row.querySelectorAll("[data-a]")) input.value = approval[input.dataset.a] ?? "";
      }
      $("#manual-btn").textContent = "Review sample bill";
      toast("Sample bill and estimate photos loaded. Click Read the bill to try photo reading, or Review sample bill to inspect the prefilled amounts.");
    } catch (err) { toast(err.message); }
  });
  bindThumbs("#bill-files", "#bill-drop");
  bindThumbs("#estimate-files", "#estimate-drop");
  $("#add-approval").addEventListener("click", () => approvalRow());
  $$("[data-sample]").forEach((b) => b.addEventListener("click", async () => {
    try {
      const blob = await (await fetch(`/samples/${b.dataset.sample}`)).blob();
      const dt = new DataTransfer();
      dt.items.add(new File([blob], b.dataset.sample, { type: blob.type || "image/jpeg" }));
      $("#bill-files").files = dt.files;
      $("#bill-files").dispatchEvent(new Event("change"));
      const sel = $("#vehicle-select");
      if (!sel.value && S.vehicles.some((v) => v.vehicle_id === "fairfix_sample_scooter")) {
        sel.value = "fairfix_sample_scooter"; sel.dispatchEvent(new Event("change"));
      }
    } catch (err) { toast(`Couldn't load the sample photo: ${err.message}`); }
  }));
  $("#manual-btn").addEventListener("click", () => {
    if (!checkVehicle()) return;
    if (S.walkthrough) {
      const documentFromSample = (source) => {
        const doc = emptyDoc(source.kind);
        for (const key of Object.keys(doc)) {
          if (key === "lines") continue;
          if (doc[key] && !Array.isArray(doc[key]) && typeof doc[key] === "object" && "value" in doc[key]) {
            doc[key] = { value: source[key] ?? null, origin: "user_corrected", extracted_value: null };
          } else if (source[key] !== undefined) doc[key] = structuredClone(source[key]);
        }
        doc.lines = source.lines.map((line) => {
          const out = emptyLine();
          for (const [key, value] of Object.entries(line)) {
            out[key] = out[key] && typeof out[key] === "object" && "value" in out[key]
              ? { value, origin: "user_corrected", extracted_value: null } : value;
          }
          const jobs = ["engine_oil_replacement", "air_filter_replacement", "engine_flush", "brake_shoe_replacement", "periodic_service_labour", "unrecognised"];
          const index = source.lines.indexOf(line);
          out.job = jobs[index]; out.job_source = "user";
          const history = S.walkthrough.vehicle.history.find((entry) => entry.job === out.job);
          if (history) out._history = structuredClone(history);
          return out;
        });
        return doc;
      };
      S.bill = documentFromSample(S.walkthrough.bill);
      S.estimate = documentFromSample(S.walkthrough.estimate);
      S.flags = []; S.readTrace = null;
      renderCorrection(["Fictional sample entered for review, not read by AI. Review or edit the amounts, then click Run the checks."]);
      show("correct");
      return;
    }
    S.bill = emptyDoc("bill"); S.bill.lines.push(emptyLine());
    S.estimate = null; S.flags = []; S.readTrace = null;
    renderCorrection(["Typed in by you: every value is marked as entered by you."]);
    show("correct");
  });
  $("#vehicle-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!checkVehicle()) return;
    const bill = $("#bill-files").files;
    if (!bill.length) return vehicleError("Add at least one photo of the bill, or type the bill in instead.");
    const fd = new FormData();
    [...bill].forEach((f) => fd.append("bill", f));
    [...$("#estimate-files").files].forEach((f) => fd.append("estimate", f));
    const pages = bill.length + $("#estimate-files").files.length;
    $("#scan-img").src = URL.createObjectURL(bill[0]);
    $("#scan-detail").textContent = `Reading ${pages} page${pages === 1 ? "" : "s"}: every line and amount exactly as printed, leaving out names and numbers.`;
    show("reading");
    $$("button.primary").forEach((b) => (b.disabled = true));
    try {
      const out = await api("/api/extract", { method: "POST", body: fd });
      S.bill = out.bill; S.estimate = out.estimate; S.flags = out.flags || []; S.readTrace = out.trace || null;
      renderCorrection(out.warnings || []);
      show("correct");
    } catch (err) {
      show("vehicle");
      vehicleError(`${err.message} You can also type the bill in instead.`);
    } finally { $$("button.primary").forEach((b) => (b.disabled = false)); }
  });
}

function checkVehicle() {
  $("#vehicle-error").hidden = true;
  if (!$("#vehicle-select").value) { vehicleError("Choose your vehicle, or pick Another vehicle."); return false; }
  return true;
}
function vehicleError(msg) { const e = $("#vehicle-error"); e.textContent = msg; e.hidden = false; e.scrollIntoView({ block: "center" }); }

/* ------------------------------------------------------------------ correction */

const fld = (v = null) => ({ value: v, origin: "extracted", extracted_value: v });
function emptyLine() {
  return { raw_text: fld(""), qty: fld(), unit_price_inr: fld(), line_total_inr: fld(), part_number: fld(), gst_included: fld(),
           kind: "unknown", grade: "unknown", job: null, job_confidence: null, job_source: "none" };
}
function emptyDoc(kind) {
  return { kind, doc_date: fld(), odometer_km: fld(), job_card_no: fld(), lines: [], subtotal_inr: fld(), discount_inr: fld(),
           discount_applied: fld(), prices_include_tax: fld(), tax_lines: [], round_off_inr: fld(), grand_total_inr: fld(),
           extraction_notes: [] };
}

function setField(field, value, input) {
  field.value = value;
  const changed = JSON.stringify(field.extracted_value ?? null) !== JSON.stringify(value ?? null);
  field.origin = changed ? "user_corrected" : "extracted";
  if (input) input.classList.toggle("corrected", changed);
}

function jobOptions(select, current) {
  select.replaceChildren(el("option", { value: "unrecognised", text: "Not sure (ask the advisor)" }));
  const groups = { maintenance: "Scheduled maintenance", wear_repair: "Repairs on condition", add_on: "Optional treatments",
                   labour: "Labour", misc: "Small consumables" };
  for (const [kind, name] of Object.entries(groups)) {
    const og = el("optgroup", { label: name });
    S.jobs.filter((j) => j.kind === kind).forEach((j) => og.append(el("option", { value: j.id, text: j.label })));
    select.append(og);
  }
  select.value = current && S.jobsById[current] ? current : "unrecognised";
}

function renderCorrection(warnings) {
  const d = S.bill;
  const notes = $("#extract-notes");
  notes.replaceChildren();
  warnings.forEach((w) => notes.append(el("p", { class: "note-plain", text: w })));
  (d.extraction_notes || []).forEach((n) => notes.append(el("p", { class: "note-plain", text: `While reading: ${n}` })));
  S.flags.forEach((f) => {
    if (f.check === "reading") { notes.append(el("p", { class: "note-flag", text: f.label + "." })); return; }
    if (f.category === "needs_confirmation" || f.category === "unable_to_verify")
      notes.append(el("p", { class: "note-flag", text:
        (f.line_index !== undefined ? `Line ${f.line_index + 1}: ` : "") + f.label + (f.calculation ? ` (${f.calculation})` : "") +
        ". Check it wasn't misread." }));
  });

  $$("[data-doc]").forEach((inp) => {
    const key = inp.dataset.doc;
    const v = d[key] ? d[key].value : null;
    inp.value = v === null || v === undefined ? "" : String(v);
    inp.classList.toggle("corrected", d[key] && d[key].origin === "user_corrected");
    inp.onchange = () => {
      let val = inp.value.trim();
      if (key === "prices_include_tax") val = val === "" ? null : val === "true";
      else if (key === "doc_date" || key === "discount_applied") val = val || null;
      else val = num(val);
      setField(d[key], val, inp);
    };
  });

  const box = $("#lines");
  box.replaceChildren();
  d.lines.forEach((ln, i) => box.append(lineEditor(ln, i)));
  renderTaxes();
}

function lineEditor(ln, i) {
  const node = $("#line-tpl").content.firstElementChild.cloneNode(true);
  $("legend", node).textContent = `Line ${i + 1}`;
  if (ln.raw_text.origin === "user_corrected" && ln.raw_text.extracted_value) {
    const o = $(".orig", node); o.hidden = false; o.textContent = `Read as: ${ln.raw_text.extracted_value}`;
  }
  $$("[data-f]", node).forEach((inp) => {
    const k = inp.dataset.f;
    inp.value = ln[k].value ?? "";
    inp.classList.toggle("corrected", ln[k].origin === "user_corrected");
    inp.addEventListener("change", () => setField(ln[k], k === "raw_text" || k === "part_number" ? (inp.value.trim() || null) : num(inp.value), inp));
  });
  const jobSel = $('[data-k="job"]', node);
  jobOptions(jobSel, ln.job);
  const syncUnrec = () => node.classList.toggle("unrec", jobSel.value === "unrecognised");
  syncUnrec();
  const hist = $(".history", node);
  const showHist = () => { const j = S.jobsById[jobSel.value]; hist.hidden = !(j && j.kind === "maintenance"); };
  showHist();
  jobSel.addEventListener("change", () => {
    ln.job = jobSel.value; ln.job_source = "user"; ln.job_confidence = 1;
    syncUnrec(); showHist();
    const j = S.jobsById[ln.job];
    if (j && j.kind === "labour") { ln.kind = "labour"; $('[data-k="kind"]', node).value = "labour"; }
  });
  ["kind", "grade"].forEach((k) => {
    const s = $(`[data-k="${k}"]`, node);
    s.value = ln[k] || "unknown";
    s.addEventListener("change", () => (ln[k] = s.value));
  });
  ln._history = ln._history || { status: "unknown", km: null, on: null };
  const hs = $('[data-h="status"]', hist);
  hs.value = ln._history.status;
  const when = $(".when", hist);
  const syncWhen = () => (when.hidden = hs.value !== "replaced");
  syncWhen();
  hs.addEventListener("change", () => { ln._history.status = hs.value; syncWhen(); });
  $('[data-h="km"]', hist).addEventListener("change", (e) => (ln._history.km = num(e.target.value)));
  $('[data-h="on"]', hist).addEventListener("change", (e) => (ln._history.on = e.target.value || null));
  $(".remove", node).addEventListener("click", () => {
    S.bill.lines.splice(S.bill.lines.indexOf(ln), 1);
    renderCorrection([]);
  });
  return node;
}

function renderTaxes() {
  const box = $("#taxes");
  box.replaceChildren();
  S.bill.tax_lines.forEach((t, i) => {
    const row = el("div", { class: "row three" },
      el("label", {}, "Tax", el("input", { value: t.name || "", onchange: (e) => (t.name = e.target.value) })),
      el("label", {}, "Rate %", el("input", { inputmode: "decimal", value: t.rate_pct ?? "", onchange: (e) => (t.rate_pct = num(e.target.value)) })),
      el("label", {}, "Amount", el("input", { inputmode: "decimal", value: t.amount_inr ?? "", onchange: (e) => (t.amount_inr = num(e.target.value)) })));
    box.append(row, el("button", { type: "button", class: "btn quiet small", onclick: () => { S.bill.tax_lines.splice(i, 1); renderTaxes(); } }, "Remove tax line"));
  });
}

function bindCorrection() {
  $("#add-line").addEventListener("click", () => {
    const ln = emptyLine();
    S.bill.lines.push(ln);
    $("#lines").append(lineEditor(ln, S.bill.lines.length - 1));
  });
  $("#add-tax").addEventListener("click", () => { S.bill.tax_lines.push({ name: "GST", rate_pct: null, amount_inr: null }); renderTaxes(); });
  $("#correct-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const vehicle = readVehicle();
    const bill = structuredClone(S.bill);
    bill.lines.forEach((ln) => {
      if (ln._history && ln._history.status !== "unknown" && ln.job) {
        vehicle.history.push({ job: ln.job, status: ln._history.status, km: ln._history.km, on: ln._history.on });
      }
      delete ln._history;
      if (!ln.job || ln.job_source === "none") ln.job_source = ln.job ? "user" : "none";
    });
    if (vehicle.odometer_km === null && bill.odometer_km.value !== null) vehicle.odometer_km = bill.odometer_km.value;
    const body = { bill, estimate: S.estimate, vehicle, approvals: readApprovals(), lang: "en" };
    const op = $("#oldpart-file").files[0];
    if (op) {
      body.old_part_image_b64 = await fileB64(op);
      body.old_part_mime = op.type;
      body.old_part_name = $("#oldpart-name").value.trim() || "part";
    }
    busy(true, "Checking every line against the schedule, prices, warranty and your estimate");
    try {
      S.report = await api("/api/audit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      S.demo = false; S.lang = "en";
      show("result");
      renderResult();
    } catch (err) {
      toast(`The checks couldn't run: ${err.message}`);
    } finally { busy(false); }
  });
}

function fileB64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1]);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

/* ------------------------------------------------------------------ result */

const CHECK_NAMES = { schedule: "Service schedule", price: "Price", coverage: "Warranty or recall",
                      authorization: "Your approval", arithmetic: "Arithmetic" };
const CAT_NAMES = { supported: "Supported by evidence", needs_confirmation: "Needs confirmation",
                    unable_to_verify: "Unable to verify", not_applicable: "Not applicable" };
const CAT_ICON = { supported: ICONS.tick, needs_confirmation: ICONS.ask, unable_to_verify: ICONS.dash, not_applicable: "" };
const ORDER = { needs_confirmation: 0, supported: 1, unable_to_verify: 2, not_applicable: 3 };

function rowState(findings) {
  if (findings.some((f) => f.category === "needs_confirmation")) return "ask";
  const real = findings.filter((f) => f.category !== "not_applicable" && f.check !== "arithmetic");
  if (real.length && real.every((f) => f.category === "unable_to_verify")) return "unk";
  return "ok";
}
const MARKS = { ask: ["ask about this", ICONS.ask], ok: ["nothing to ask", ICONS.tick], unk: ["couldn't check this", ICONS.dash] };

function findingNode(f) {
  const ev = (f.evidence || []).map((x) => el("div", { class: "evidence" },
    x.excerpt ? el("q", { text: x.excerpt }) : (x.data && x.data.note ? el("span", { text: x.data.note }) : null),
    el("span", { class: "meta" },
      x.source_url ? el("a", { href: x.source_url, target: "_blank", rel: "noopener" }, sourceName(x.source_url)) : "No link",
      x.checked_on ? `, checked ${x.checked_on}` : "", x.from_cache ? " (cached)" : "",
      x.sufficient ? "" : ", secondary source: can't decide on its own")));
  return el("div", { class: "finding" },
    el("span", { class: "check", text: CHECK_NAMES[f.check] || f.check }), " ",
    el("span", { class: `cat ${f.category}`, html: CAT_ICON[f.category] }, CAT_NAMES[f.category]),
    el("p", { class: "label", text: f.label }),
    f.calculation ? el("p", { class: "calc", text: f.calculation }) : null,
    f.uncertainty ? el("p", { text: f.uncertainty }) : null,
    ev,
    f.next_step ? el("p", { class: "next", text: f.next_step }) : null,
    f.verify_url ? el("button", { class: "btn quiet small", type: "button", onclick: () => openVerify(f.verify_url) }, "Check with the manufacturer") : null,
    el("details", { class: "trail" }, el("summary", {}, "Rule and inputs"),
      el("pre", { text: `rule: ${f.rule}\ninputs: ${JSON.stringify(f.inputs, null, 1)}` })));
}

function sourceName(url) { try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return "source"; } }

function countUp(node, to, fmt, ms = 900) {
  if (REDUCED || !to || document.hidden) { node.textContent = fmt(to); return; }
  const t0 = performance.now();
  const tick = (t) => {
    const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 3);
    node.textContent = fmt(to * e);
    if (p < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  setTimeout(() => (node.textContent = fmt(to)), ms + 300);
}

function renderResult() {
  const R = S.report;
  $("#result-steps").hidden = S.demo;
  const banner = $("#result-banner");
  banner.replaceChildren();
  R.warnings.filter((w) => w.toLowerCase().includes("fictional")).forEach((w) => banner.append(el("p", { class: "banner", text: w })));
  if (R.partial_audit) banner.append(el("p", { class: "banner partial", text: R.partial_reason }));
  R.warnings.filter((w) => !w.toLowerCase().includes("fictional")).forEach((w) => banner.append(el("p", { class: "note-plain", text: w })));

  const askLines = R.lines.filter((l) => rowState(l.findings) === "ask");
  const billAsk = R.bill_findings.some((f) => f.category === "needs_confirmation");
  const unk = R.lines.filter((l) => rowState(l.findings) === "unk").length;
  const billUnk = R.bill_findings.some((f) => f.category === "unable_to_verify");
  const head = $("#res-h");
  head.replaceChildren();
  if (askLines.length) {
    const n = el("span", { class: "num" }), amt = el("span", { class: "num" });
    head.append("Ask about ", n, ` ${askLines.length === 1 ? "line" : "lines"} (`, amt, ")");
    countUp(n, askLines.length, (v) => String(Math.round(v)), 600);
    countUp(amt, R.needs_confirmation_total_inr, (v) => inr(Math.round(v)), 900);
  } else if (billAsk) head.textContent = "Ask how the total was worked out";
  else head.textContent = unk || billUnk || R.partial_audit ? "Nothing flagged, but some checks couldn't run" : "Nothing on this bill needs a question";
  const plural = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  $("#result-sub").textContent = `FairFix checked ${plural(R.lines.length, "line", "lines")}. ` +
    (unk ? `${plural(unk, "line", "lines")} couldn't be checked against reliable sources, so ${unk === 1 ? "it isn't" : "they aren't"} flagged either way. ` : "") +
    (billUnk ? "The total couldn't be fully checked. " : "") + "Tap any line to see the evidence and the rule behind each result.";

  const slip = $("#slip");
  slip.replaceChildren(el("div", { class: "slip-meta" },
    el("span", { text: S.demo ? "Sample bill" : "Your bill" }), el("span", { text: `${R.lines.length} lines checked` })));
  let marked = 0;
  R.lines.forEach((lr, i) => {
    const st = rowState(lr.findings);
    const [markText, markIcon] = MARKS[st];
    const body = el("div", { class: "findings", hidden: true, id: `f-${lr.index}` },
      [...lr.findings].sort((a, b) => ORDER[a.category] - ORDER[b.category]).map(findingNode));
    const amt = el("span", { class: "amt", text: inr(lr.item.line_total_inr.value) });
    if (st === "ask") amt.insertAdjacentHTML("beforeend", ICONS.pen);
    const btn = el("button", { "aria-expanded": "false", "aria-controls": `f-${lr.index}` },
      el("span", { class: "text" }, lr.item.raw_text.value || "(no text)",
        el("span", { class: "job", text: lr.item.job === "unrecognised" ? "Not recognised" : lr.job_label })),
      amt,
      el("span", { class: "mark", html: markIcon }, markText, el("span", { html: ICONS.chev, style: "display:contents" })));
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!open));
      body.hidden = open;
    });
    const row = el("div", { class: `slip-row ${st}`, style: `--i:${i};--n:${R.lines.length}` }, btn, body);
    if (st !== "ok") marked++;
    slip.append(row);
  });
  const gt = S.bill && !S.demo ? S.bill.grand_total_inr.value : null;
  slip.append(el("div", { class: "slip-total" }, el("span", {}, "Total"),
    el("span", { class: `mark ${billAsk ? "ask" : ""}`, text: billAsk ? "ask about the total" : gt !== null ? inr(gt) : "" })),
    el("div", { class: "findings" }, R.bill_findings.map(findingNode)));

  const wrap = $("#slip-wrap");
  wrap.classList.remove("printing");
  if (!REDUCED) { void wrap.offsetWidth; wrap.classList.add("printing"); }

  const obs = $("#photo-obs");
  obs.replaceChildren();
  if (R.old_part) {
    obs.append(el("div", { class: "photo-obs" },
      el("strong", { text: R.old_part.label }),
      el("p", { text: `${{ looks_worn: "Looks worn", looks_fine: "Looks fine", cannot_determine: "Cannot determine" }[R.old_part.result]}. ${R.old_part.observed}` })));
  }

  renderPipeline();
  setMessage(R.message_to_advisor, R.message_source);
  $("#disclaimer").textContent = R.disclaimer;
  $$(".lang .chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.lang === S.lang)));
}

function engineClass(name) {
  if (/nemotron/i.test(name) || /tavily/i.test(name)) return "engine";
  if (/rules|template/i.test(name)) return "engine code";
  return "engine data";
}

function fmtMs(ms) { return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : ms >= 1 ? `${Math.round(ms)} ms` : "<1 ms"; }

function renderPipeline() {
  const steps = [...(S.readTrace && !S.demo ? [S.readTrace] : []), ...(S.report.trace || [])];
  const list = $("#pipeline");
  list.replaceChildren();
  let total = 0;
  steps.forEach((t, i) => {
    total += t.skipped ? 0 : t.ms;
    list.append(el("li", { class: t.skipped ? "skip" : "", "data-i": i },
      el("span", { class: "dot", html: ICONS.dot }),
      el("span", {}, el("span", { class: "what", text: t.label }), el("br"), el("span", { class: engineClass(t.engine), text: t.engine })),
      el("span", { class: "ms", text: t.skipped ? "skipped" : fmtMs(t.ms) }),
      el("span", { class: "det", text: t.detail })));
  });
  $("#pipe-total").replaceChildren(el("span", { text: `${steps.filter((t) => !t.skipped).length} steps ran` }), el("span", { text: `${fmtMs(total)} in total` }));
  const items = $$("#pipeline li:not(.skip)");
  items.forEach((li, k) => setTimeout(() => li.classList.add("on"), REDUCED ? 0 : 900 + k * 160));
}

function setMessage(text, source) {
  $("#message").value = text;
  $("#message").lang = S.lang;
  $("#wa-msg").href = "https://wa.me/?text=" + encodeURIComponent(text);
  $("#msg-source").textContent = source === "model"
    ? `Drafted by ${S.config.engines ? S.config.engines.message : "NVIDIA Nemotron"} from the results and checked for neutral wording. Edit it as you like.`
    : "Written from the results. Edit it as you like.";
}

function bindResult() {
  $("#copy-msg").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#message").value); $("#copy-msg").textContent = "Copied"; }
    catch (_) { $("#message").select(); document.execCommand("copy"); $("#copy-msg").textContent = "Copied"; }
    setTimeout(() => ($("#copy-msg").textContent = "Copy message"), 1800);
  });
  $$(".lang .chip").forEach((c) => c.addEventListener("click", async () => {
    if (!S.report || c.dataset.lang === S.lang) return;
    S.lang = c.dataset.lang;
    $$(".lang .chip").forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
    busy(true, "Rewriting the message");
    try {
      const out = await api("/api/message", { credentials: "same-origin", method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify({ report: S.report, lang: S.lang }) });
      setMessage(out.message, out.source);
    } catch (err) { toast(err.message); } finally { busy(false); }
  }));
}

function openVerify(url) {
  const dlg = $("#vin-dialog");
  $("#vin-copy-note").hidden = true;
  dlg.returnValue = "";
  dlg.onclose = async () => {
    if (dlg.returnValue !== "open") return;
    if (S.vin) { try { await navigator.clipboard.writeText(S.vin); } catch (_) { /* ignore */ } }
    window.open(url, "_blank", "noopener");
  };
  if (S.vin) $("#vin-copy-note").hidden = false;
  dlg.showModal();
}

init();
