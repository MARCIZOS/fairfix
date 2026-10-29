/* FairFix dashboard: figures from /api/eval and /api/stats. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "style") n.setAttribute("style", v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k.nodeType ? k : String(k));
  return n;
}
const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}

const CASES = {
  hard_damaged_photo: "Stained, creased photo", hard_dealer_gst_invoice: "Dealer GST invoice", hard_thermal_receipt: "Faded thermal receipt",
  hard_hindi_items: "Bill with Hindi items", hard_handwritten: "Handwritten bill", hard_angled_photo: "Photo taken at an angle",
  hard_arithmetic_errors: "Bill with arithmetic slips", hard_estimate_vs_bill: "Bill against its estimate",
  hard_handwritten_addition: "Handwritten additions", hard_multipage_car: "Two-page car bill",
};
const caseName = (k) => CASES[k] || k.replace(/^hard_|^sample_/, "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const pct = (f) => (f && f.pct !== null ? f.pct : null);
const fmtPct = (v) => (v === null ? "–" : (Number.isInteger(v) ? v : v.toFixed(1)) + "%");

/* ---------------------------------------------------------------- tooltip */
const tip = $("#tip");
function hoverTip(node, html) {
  const move = (e) => {
    const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + "px"; tip.style.top = (e.clientY + 16) + "px";
  };
  node.addEventListener("pointerenter", (e) => { tip.replaceChildren(...html()); tip.hidden = false; move(e); });
  node.addEventListener("pointermove", move);
  node.addEventListener("pointerleave", () => (tip.hidden = true));
}

/* ---------------------------------------------------------------- count-up */
function countUp(node, to, decimals = 0, ms = 1100) {
  const fmt = (v) => v.toFixed(decimals) + (node.dataset.suffix || "");
  // Background tabs pause animation frames, so show the final value straight away there.
  if (REDUCED || to === null || document.hidden) { node.textContent = to === null ? "–" : fmt(to); return; }
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 3);
    node.textContent = fmt(to * e);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  setTimeout(() => (node.textContent = fmt(to)), ms + 300);   // safety net if frames were paused
}

/* reveal sections as they scroll into view */
const io = "IntersectionObserver" in window && !REDUCED
  ? new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: .25 })
  : null;
const reveal = (n) => (io ? io.observe(n) : n.classList.add("in"));

function table(head, rows) {
  return el("div", { class: "scroll" }, el("table", {},
    el("thead", {}, el("tr", {}, head.map((h) => el("th", { scope: "col", text: h })))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c, i) => el(i ? "td" : "th", i ? {} : { scope: "row" }, c)))))));
}

/* ---------------------------------------------------------------- sections */
function renderHead(c) {
  $("#run-line").textContent = `Latest run ${c.run} on ${c.cases} bills, ${c.mode.toLowerCase()}. ` +
    "Each bill has a hand-written answer key for every line, so these figures are counted, not estimated.";
  const h = $("#honest");
  h.hidden = false;
  h.textContent = c.real ? `${c.real} of the ${c.cases} bills are real; ${c.mock} are generated.` :
    `All ${c.cases} bills are generated test bills (0 real yet), made hard on purpose: faded print, stains, handwriting, Hindi items. Treat these figures as a first check, not a guarantee.`;

  const pass = c.gate === "PASS";
  const stamp = $("#stamp");
  $("#gate-word").textContent = pass ? "Passed" : "Failed";
  $("#gate-date").textContent = c.run.split(" ")[0];
  stamp.classList.toggle("fail", !pass);
  stamp.setAttribute("aria-label", `Release gate ${pass ? "passed" : "failed"}`);
  $("#hero-pct").dataset.suffix = "%";
  countUp($("#hero-pct"), pct(c.fields_read), 1);
  $("#hero-zero").textContent = c.unsupported_flags ?? "–";
  $("#g-unsup").textContent = c.unsupported_flags ?? "–";
  $("#g-acc").textContent = c.accusatory ?? "–";
  const go = () => { stamp.classList.add("down"); $("#gate").classList.add("thud"); };
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => setTimeout(go, REDUCED ? 0 : 450));
}

function renderKpis(c, b) {
  const f = c.fields_read;
  countUp($("#k-fields"), pct(f), 1);
  $("#k-fields-sub").textContent = `${f.ok} of ${f.total} lines, amounts, quantities, totals and tax details` +
    (b && b.fields_read ? `, up from ${fmtPct(pct(b.fields_read))} before the fixes` : "");
  if (c.latency_s) {
    countUp($("#k-lat"), c.latency_s.median, 1);
    $("#k-lat-sub").textContent = `Read, check and draft the message. Slowest: ${c.latency_s.slowest} s`;
  }
  const calls = c.model_calls.reduce((a, r) => a + r.calls, 0), first = c.model_calls.reduce((a, r) => a + (r.ok_first || 0), 0);
  countUp($("#k-calls"), first, 0);
  $("#k-calls-of").textContent = ` of ${calls}`;
  $("#k-calls-sub").textContent = c.model_calls.every((r) => !r.retry_ok && !r.unusable) ? "No retries or unusable replies" :
    "Some calls needed a retry";
  countUp($("#k-priv"), c.personal_data_kept, 0, 500);
  $("#k-priv-sub").textContent = "Names, phone numbers and registrations are removed before anything is shown" +
    (b && b.personal_data_kept ? ` (was ${b.personal_data_kept} before the fixes)` : "");
}

function renderMeters(c) {
  const box = $("#meters");
  c.accuracy.forEach((a) => {
    const row = (cls, f, label) => {
      const w = f && f.total ? f.ok / f.total : 0;
      const hit = el("span", { class: "hit" });
      const bar = el("div", { class: `bar ${cls}` },
        el("span", { class: "tr" }, el("span", { class: "fill", style: `--w:${w}` }), hit),
        el("span", { class: "v", text: f ? fmtPct(f.pct) : "–" }));
      if (f) hoverTip(bar, () => [el("b", { text: a.measure }), `${label}: ${f.ok} of ${f.total} (${fmtPct(f.pct)})`]);
      return bar;
    };
    box.append(el("div", { class: "meter" }, el("span", { class: "name", text: a.measure }),
      el("div", { class: "bars" }, row("a", a.after_correction, "After correction"), row("b", a.from_photo, "Straight from the photo"))));
  });
  reveal(box);
  $("#acc-table").append(table(["Check", "After correction", "From the photo"],
    c.accuracy.map((a) => [a.measure, `${a.after_correction.ok}/${a.after_correction.total}`, a.from_photo ? `${a.from_photo.ok}/${a.from_photo.total}` : "–"])));
}

function dumbbell(name, from, to) {
  const lo = Math.max(0, Math.floor((Math.min(from, to) - 10) / 10) * 10);
  const W = 300, x = (v) => 8 + ((v - lo) / (100 - lo)) * (W - 16);
  const s = svg("svg", { viewBox: `0 0 ${W} 46`, preserveAspectRatio: "none", role: "img",
                         "aria-label": `${name}: ${fmtPct(from)} before, ${fmtPct(to)} after` });
  s.append(svg("line", { class: "axis", x1: 8, x2: W - 8, y1: 18, y2: 18 }));
  [lo, 100].forEach((t) => s.append(svg("text", { class: "tick", x: x(t), y: 42, "text-anchor": t === lo ? "start" : "end" }, t + "%")));
  s.append(svg("line", { class: "link", x1: x(from), x2: x(to), y1: 18, y2: 18, pathLength: 1 }));
  s.append(svg("circle", { class: "from", cx: x(from), cy: 18, r: 6 }));
  s.append(svg("circle", { class: "to", cx: x(to), cy: 18, r: 7 }));
  const close = Math.abs(x(to) - x(from)) < 44, up = to >= from;
  s.append(svg("text", { x: close ? x(from) + (up ? 4 : -4) : x(from), y: 7, "text-anchor": close ? (up ? "end" : "start") : "middle" }, fmtPct(from)));
  s.append(svg("text", { class: "t", x: close ? x(to) + (up ? 4 : -4) : x(to), y: 7, "text-anchor": close ? (up ? "start" : "end") : "middle" }, fmtPct(to)));
  const wrap = el("div", { class: "db" }, el("div", { class: "name", text: name }), s);
  hoverTip(wrap, () => [el("b", { text: name }), `Before the fixes: ${fmtPct(from)}`, el("br"), `After: ${fmtPct(to)}`]);
  return wrap;
}

function renderBeforeAfter(c, b) {
  const box = $("#dumbbells");
  if (!b) { box.append(el("p", { class: "hint", text: "No earlier run to compare with." })); return; }
  const pairs = [["Fields read correctly", pct(b.fields_read), pct(c.fields_read)]];
  c.accuracy.forEach((a) => {
    const old = b.accuracy.find((x) => x.measure === a.measure);
    if (old && old.from_photo && a.from_photo && old.from_photo.pct !== a.from_photo.pct)
      pairs.push([`${a.measure}, from the photo`, old.from_photo.pct, a.from_photo.pct]);
  });
  pairs.forEach(([n, f, t]) => box.append(dumbbell(n, f, t)));
  legendFor(box);
  const counts = [
    ["False flags straight from the photo", b.photo_flags.length, c.photo_flags.length],
    ["Reading misses", b.reading_misses.length, c.reading_misses.length],
    ["Personal details left in", b.personal_data_kept, c.personal_data_kept],
  ];
  const d = $("#deltas");
  counts.forEach(([l, f, t]) => d.append(el("div", { class: "delta" },
    el("div", { class: "n" }, el("s", { text: f }), el("span", { "aria-hidden": "true", text: "→ " }), t),
    el("div", { class: "l", text: l }))));
  reveal($("#dumbbells").closest(".card"));
  $("#ba-table").append(table(["Measure", "Before", "After"],
    [...pairs.map(([n, f, t]) => [n, fmtPct(f), fmtPct(t)]), ...counts.map(([n, f, t]) => [n, f, t])]));
}

function legendFor(box) {
  box.prepend(el("ul", { class: "legend" }, el("li", {}, el("span", { class: "sw b", style: "border-radius:50%" }), "Before the fixes"),
    el("li", {}, el("span", { class: "sw a", style: "border-radius:50%" }), "After")));
}

const STEP_NAMES = { extract: ["Read photos", "vision model"], message: ["Draft message", "text model"],
                     normalise: ["Match items", "text model"] };
const MODEL_NAMES = { "nvidia/Nemotron-3.5-Super-VL": "Nemotron 3.5 Super VL", "nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super 120B" };

function renderCalls(st) {
  const box = $("#hbars");
  const steps = st.by_step.filter((s) => STEP_NAMES[s.step]);
  if (!steps.length) { box.append(el("p", { class: "hint", text: "No model calls logged yet. Run the app or the evaluation with your Nebius key." })); return; }
  const max = Math.max(...steps.map((s) => s.p90_ms || s.median_ms));
  steps.forEach((s) => {
    const [n, m] = STEP_NAMES[s.step];
    const bar = el("div", { class: "bar a" },
      el("span", { class: "tr" }, el("span", { class: "fill", style: `--w:${s.median_ms / max}` }), el("span", { class: "hit" })),
      el("span", { class: "v", text: (s.median_ms / 1000).toFixed(1) + " s" }));
    hoverTip(bar, () => [el("b", { text: `${n} (${m})` }), `${s.calls} calls · median ${(s.median_ms / 1000).toFixed(2)} s · 90% under ${(s.p90_ms / 1000).toFixed(1)} s`,
      el("br"), `${(s.prompt_tokens + s.completion_tokens).toLocaleString("en-IN")} tokens in total`]);
    box.append(el("div", { class: "hb" }, el("span", { class: "name" }, n, el("small", { text: `${s.calls} calls` })), bar));
  });
  reveal(box);
  const errs = Object.entries(st.outcomes).filter(([k]) => k !== "ok").reduce((a, [, v]) => a + v, 0);
  $("#models").append(...st.by_model.map((m) => el("div", { class: "model" },
    el("strong", { text: MODEL_NAMES[m.model] || m.model }),
    el("span", { text: `${m.calls} calls · median ${(m.median_ms / 1000).toFixed(1)} s · ${(m.prompt_tokens + m.completion_tokens).toLocaleString("en-IN")} tokens` }))),
    el("p", { class: "hint", style: "margin:.6rem 0 0", text: `${st.total_calls} answered calls logged` +
      (errs ? `; ${errs} failed at the provider (connection or service errors)` + (st.last_error ? `, the most recent on ${st.last_error.slice(0, 10)}.` : ".") : ", none failed.") }));
  $("#calls-table").append(table(["Step", "Calls", "Median", "90th percentile", "Tokens"],
    steps.map((s) => [STEP_NAMES[s.step][0], s.calls, `${s.median_ms} ms`, `${s.p90_ms} ms`, (s.prompt_tokens + s.completion_tokens).toLocaleString("en-IN")])));
}

function plainMiss(m) {
  const i = m.indexOf(": ");
  const key = m.slice(0, i), rest = m.slice(i + 2);
  let what = rest
    .replace(/^privacy: PERSONAL DATA KEPT: (.*)$/, "A name was left in the reading: $1")
    .replace(/expected None, got ([\d.]+) \(printed value is hidden: it should be null, not guessed\)/, "is hidden by a stain, but the model guessed ₹$1 instead of leaving it blank")
    .replace(/expected ([\d.]+), got None/, "was printed as ₹$1 but couldn't be read")
    .replace(/expected ([\d.]+), got ([\d.]+)/, (_, a, b2) => `read as ₹${+b2}, printed ₹${+a}`)
    .replace(/^grand_total/, "Grand total").replace(/₹(\d+)\.0\b/g, "₹$1")
    .replace(/^prices include GST: expected True, got None/, "Missed that prices already include GST");
  what = what.charAt(0).toUpperCase() + what.slice(1);
  return el("li", {}, el("div", {}, el("span", { class: "case", text: caseName(key) }), el("span", { class: "what", text: what })));
}

async function main() {
  try {
    const [ev, st] = await Promise.all([fetch("/api/eval").then((r) => r.json()), fetch("/api/stats").then((r) => r.json())]);
    const c = ev.current, b = ev.before_fixes;
    if (!c) { $("#run-line").textContent = "No evaluation results yet. Run python -m eval.run_eval to create them."; return; }
    renderHead(c); renderKpis(c, b); renderMeters(c); renderBeforeAfter(c, b); renderCalls(st);
    const ms = $("#misses");
    if (c.reading_misses.length) c.reading_misses.forEach((m) => ms.append(plainMiss(m)));
    else ms.append(el("li", {}, el("span", { class: "what", text: "No reading misses in the latest run." })));
  } catch (err) {
    $("#run-line").replaceChildren(el("span", { class: "error", text: `Couldn't load the figures: ${err.message}` }));
  }
}
main();
