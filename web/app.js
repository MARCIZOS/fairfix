/* FairFix frontend. Plain JS, no build step.
   Bill data lives in this page's memory only; nothing is saved to the browser. */
"use strict";

const S = { vehicles: [], jobs: [], jobsById: {}, config: {}, bill: null, estimate: null, flags: [],
            report: null, lang: "en", demo: false, vin: "" };

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
}

const inr = (x) => (x === null || x === undefined || x === "") ? "?" :
  "₹" + Number(x).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: Number(x) % 1 ? 2 : 0 });

function num(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/[^\d.\-]/g, "");
  if (s === "" || s === "-" || s === ".") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch (_) { /* empty */ }
  if (!r.ok) {
    const msg = (body && (body.detail || body.error)) || `Request failed (${r.status})`;
    throw new Error(typeof msg === "string" ? msg : "Request failed");
  }
  return body;
}

function busy(on, text = "Working") {
  $("#busy").hidden = !on;
  $("#busy-text").textContent = text;
  $$("button.primary").forEach((b) => (b.disabled = on));
}

function show(view) {
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${view}`));
  window.scrollTo({ top: 0 });
  const h = $(`#view-${view} h1, #view-${view} h2`);
  if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
}

/* ------------------------------------------------------------------ home */

async function init() {
  document.addEventListener("click", (e) => {
    const go = e.target.closest("[data-go]");
    if (go) { e.preventDefault(); show(go.dataset.go); }
  });
  try {
    const [cfg, vehicles, jobs, demos] = await Promise.all([
      api("/api/config"), api("/api/vehicles"), api("/api/jobs"), api("/api/demo")]);
    S.config = cfg; S.vehicles = vehicles; S.jobs = jobs;
    jobs.forEach((j) => (S.jobsById[j.id] = j));
    $("#privacy-note").textContent = cfg.privacy +
      ` Prices are flagged only when more than ${cfg.price_review_threshold_pct}% above the highest of at least ` +
      `${cfg.price_min_comparables} equivalent listings from ${cfg.price_min_sellers} or more sellers.`;
    renderDemos(demos);
    renderVehicleSelect();
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
  box.append(el("p", { class: "hint", text: "Or see how it works on a sample bill. The vehicle and its reference facts are fictional." }));
  demos.forEach((d) => box.append(
    el("button", { class: "demo-item", onclick: () => runDemo(d.name) }, el("strong", { text: d.title }), el("span", { text: d.description }))));
}

async function runDemo(name) {
  busy(true, "Running the sample through the checks");
  try {
    const out = await api(`/api/demo/${encodeURIComponent(name)}`);
    S.demo = true; S.lang = "en";
    S.report = out.report;
    renderResult();
    show("result");
  } catch (err) {
    alert(err.message);
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

function bindVehicleForm() {
  $("#add-approval").addEventListener("click", () => approvalRow());
  $("#manual-btn").addEventListener("click", () => {
    if (!checkVehicle()) return;
    S.bill = emptyDoc("bill"); S.bill.lines.push(emptyLine());
    S.estimate = null; S.flags = [];
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
    busy(true, "Reading the bill");
    try {
      const out = await api("/api/extract", { method: "POST", body: fd });
      S.bill = out.bill; S.estimate = out.estimate; S.flags = out.flags || [];
      renderCorrection(out.warnings || []);
      show("correct");
    } catch (err) {
      vehicleError(`${err.message} You can also type the bill in instead.`);
    } finally { busy(false); }
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
    busy(true, "Running the checks");
    try {
      S.report = await api("/api/audit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      S.demo = false; S.lang = "en";
      renderResult();
      show("result");
    } catch (err) {
      alert(`The checks couldn't run: ${err.message}`);
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
const ORDER = { needs_confirmation: 0, supported: 1, unable_to_verify: 2, not_applicable: 3 };

function rowState(findings) {
  if (findings.some((f) => f.category === "needs_confirmation")) return "ask";
  const real = findings.filter((f) => f.category !== "not_applicable" && f.check !== "arithmetic");
  if (real.length && real.every((f) => f.category === "unable_to_verify")) return "unk";
  return "ok";
}
const MARKS = { ask: "ask about this", ok: "nothing to ask", unk: "couldn't check this" };

function findingNode(f) {
  const ev = (f.evidence || []).map((x) => el("div", { class: "evidence" },
    x.excerpt ? el("q", { text: x.excerpt }) : (x.data && x.data.note ? el("span", { text: x.data.note }) : null),
    el("span", { class: "meta" },
      x.source_url ? el("a", { href: x.source_url, target: "_blank", rel: "noopener" }, sourceName(x.source_url)) : "No link",
      x.checked_on ? `, checked ${x.checked_on}` : "", x.from_cache ? " (cached)" : "",
      x.sufficient ? "" : ", secondary source: can't decide on its own")));
  return el("div", { class: "finding" },
    el("span", { class: "check", text: CHECK_NAMES[f.check] || f.check }), " ",
    el("span", { class: `cat ${f.category}`, text: CAT_NAMES[f.category] }),
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
  const head = $("#res-h");
  if (askLines.length || billAsk) {
    head.textContent = askLines.length
      ? `Ask about ${askLines.length} ${askLines.length === 1 ? "line" : "lines"} (${inr(R.needs_confirmation_total_inr)})`
      : "Ask how the total was worked out";
  }
  const unk = R.lines.filter((l) => rowState(l.findings) === "unk").length;
  const billUnk = R.bill_findings.some((f) => f.category === "unable_to_verify");
  if (!askLines.length && !billAsk) {
    head.textContent = unk || billUnk || R.partial_audit
      ? "Nothing flagged, but some checks couldn't run"
      : "Nothing on this bill needs a question";
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  $("#result-sub").textContent = `FairFix checked ${plural(R.lines.length, "line", "lines")}. ` +
    (unk ? `${plural(unk, "line", "lines")} couldn't be checked against reliable sources, so ${unk === 1 ? "it isn't" : "they aren't"} flagged either way. ` : "") +
    (billUnk ? "The total couldn't be fully checked. " : "") +
    "Tap any line to see the evidence and the rule behind each result.";

  const slip = $("#slip");
  slip.replaceChildren();
  R.lines.forEach((lr) => {
    const st = rowState(lr.findings);
    const body = el("div", { class: "findings", hidden: true, id: `f-${lr.index}` },
      [...lr.findings].sort((a, b) => ORDER[a.category] - ORDER[b.category]).map(findingNode));
    const btn = el("button", { "aria-expanded": "false", "aria-controls": `f-${lr.index}` },
      el("span", { class: "text" }, lr.item.raw_text.value || "(no text)",
        el("span", { class: "job", text: lr.item.job === "unrecognised" ? "Not recognised" : lr.job_label })),
      el("span", { class: "amt", text: inr(lr.item.line_total_inr.value) }),
      el("span", { class: "mark", text: MARKS[st] }));
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!open));
      body.hidden = open;
    });
    slip.append(el("div", { class: `slip-row ${st}` }, btn, body));
  });
  const totalBody = el("div", { class: "findings" }, R.bill_findings.map(findingNode));
  const gt = S.bill && !S.demo ? S.bill.grand_total_inr.value : null;
  slip.append(el("div", { class: "slip-total" }, el("span", {}, "Total"),
    el("span", { class: "mark", text: billAsk ? "ask about the total" : gt !== null ? inr(gt) : "" })), totalBody);

  if (R.old_part) {
    slip.after(el("div", { class: "photo-obs" },
      el("strong", { text: R.old_part.label }),
      el("p", { text: `${{ looks_worn: "Looks worn", looks_fine: "Looks fine", cannot_determine: "Cannot determine" }[R.old_part.result]}. ${R.old_part.observed}` })));
  }

  setMessage(R.message_to_advisor, R.message_source);
  $("#disclaimer").textContent = R.disclaimer;
  $$(".lang .chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.lang === S.lang)));
}

function setMessage(text, source) {
  $("#message").value = text;
  $("#message").lang = S.lang;
  $("#wa-msg").href = "https://wa.me/?text=" + encodeURIComponent(text);
  $("#msg-source").textContent = source === "model"
    ? "Drafted by NVIDIA Nemotron from the results above and checked for neutral wording. Edit it as you like."
    : "Written from the results above. Edit it as you like.";
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
      const out = await api("/api/message", { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify({ report: S.report, lang: S.lang }) });
      setMessage(out.message, out.source);
    } catch (err) { alert(err.message); } finally { busy(false); }
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
