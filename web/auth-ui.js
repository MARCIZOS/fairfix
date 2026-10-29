/* FairFix account card: sign in, create account, signed-in state, delete account.
   Shared by the home page (section under the hero) and /signin.html.
   The session lives in an HttpOnly cookie; this script only ever sees "signed in as <email>". */
"use strict";

window.FFAuth = (() => {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let session = { required: true, signed_in: false, email: null, invite_required: false };
  const listeners = [];

  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v === null || v === undefined) continue;
      if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    kids.flat().forEach((k) => k !== null && k !== undefined && n.append(k));
    return n;
  };
  const TICK = () => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 20 20");
    s.setAttribute("aria-hidden", "true"); s.innerHTML = '<path d="M3 11 L8 15.5 L17 4"/>'; return s; };

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof b.detail === "string" ? b.detail : "Something went wrong. Try again.");
    return b;
  }

  function set(s) { session = s; listeners.forEach((fn) => fn(session)); }

  async function refresh() {
    try { set(await (await fetch("/api/session", { credentials: "same-origin" })).json()); }
    catch (_) { /* server unreachable: keep the last state */ }
    return session;
  }

  async function signOut() { set(await post("/api/logout")); }

  /* Render the card into `root`. opts.afterSignIn() runs after a successful sign-in or sign-up. */
  function mount(root, opts = {}) {
    let mode = "signin";
    const uid = root.id || "auth";

    function field(label, input, extra) {
      return h("div", { class: "af" }, h("label", { for: input.id, text: label }), input, extra || null);
    }

    function formView() {
      const create = mode === "create";
      const email = h("input", { id: `${uid}-email`, type: "email", name: "email", autocomplete: "email", required: true, inputmode: "email", spellcheck: "false" });
      const pw = h("input", { id: `${uid}-pw`, type: "password", name: "password", required: true, minlength: create ? "8" : null,
        autocomplete: create ? "new-password" : "current-password" });
      const eye = h("button", { type: "button", class: "eye", "aria-pressed": "false", text: "Show",
        onclick: () => { const on = pw.type === "password"; pw.type = on ? "text" : "password"; eye.textContent = on ? "Hide" : "Show"; eye.setAttribute("aria-pressed", String(on)); } });
      const invite = create && session.invite_required ? h("input", { id: `${uid}-invite`, name: "invite", autocomplete: "off", spellcheck: "false" }) : null;
      const err = h("p", { class: "error", role: "alert", hidden: true });
      const go = h("button", { class: "btn primary", type: "submit", text: create ? "Create account" : "Sign in" });

      const form = h("form", { class: "af-form", novalidate: true, onsubmit: async (e) => {
        e.preventDefault();
        err.hidden = true;
        if (!email.value.trim()) { email.focus(); return; }
        if (!pw.value) { pw.focus(); return; }
        go.disabled = true;
        try {
          const body = { email: email.value, password: pw.value };
          if (invite) body.invite_code = invite.value;
          const next = await post(create ? "/api/signup" : "/api/login", body);
          mode = "signin";                    // a later sign-out lands on "Sign in", not "Create account"
          set(next);
          if (opts.afterSignIn) opts.afterSignIn(session);
        } catch (x) {
          err.textContent = x.message; err.hidden = false;
          root.classList.remove("shake"); void root.offsetWidth; if (!reduced) root.classList.add("shake");
          (/email/i.test(x.message) && !/incorrect/i.test(x.message) ? email : pw).focus();
        } finally { go.disabled = false; }
      } },
        field("Email", email),
        field("Password", pw, eye),
        create ? h("p", { class: "af-hint", text: "At least 8 characters. A short phrase you'll remember works well." }) : null,
        invite ? field("Invite code", invite) : null,
        err, go,
        create ? h("p", { class: "af-hint", text: "Your account holds your email and a hashed password, nothing else. Bills and results are never saved to it." })
               : h("p", { class: "af-hint", text: "Forgot your password? There's no email reset yet: ask the FairFix team, or create an account with another email." }));

      const tab = (m, label) => h("button", { type: "button", role: "tab", class: "af-tab", id: `${uid}-tab-${m}`, "aria-selected": String(mode === m),
        onclick: () => { if (mode !== m) { mode = m; render(); $(`#${uid}-email`).focus(); } }, text: label });
      return [h("div", { class: "af-tabs", role: "tablist", "aria-label": "Account" }, tab("signin", "Sign in"), tab("create", "Create account")), form];
    }

    function inView() {
      const pw = h("input", { id: `${uid}-delpw`, type: "password", autocomplete: "current-password" });
      const err = h("p", { class: "error", role: "alert", hidden: true });
      const del = h("details", { class: "af-del" }, h("summary", { text: "Delete my account" }),
        h("p", { class: "af-hint", text: "This removes your email, password and sessions for good." }),
        field("Confirm with your password", pw), err,
        h("button", { type: "button", class: "btn quiet small danger", text: "Delete account", onclick: async () => {
          err.hidden = true;
          try { set(await post("/api/account/delete", { password: pw.value })); }
          catch (x) { err.textContent = x.message; err.hidden = false; }
        } }));
      return [h("p", { class: "si-ok" }, TICK(), h("span", {}, "Signed in as ", h("b", { text: session.email || "" }))),
        h("div", { class: "actions" },
          opts.checkButton ? opts.checkButton() : h("a", { class: "btn primary", href: "/#check", text: "Check a bill" }),
          h("button", { type: "button", class: "btn quiet", text: "Sign out", onclick: signOut })),
        del];
    }

    function offView() {
      return [h("p", { class: "si-ok" }, TICK(), h("span", { text: "Sign-in is switched off on this server." })),
        h("p", { class: "af-hint", text: "Anyone can check a bill here. Set AUTH_REQUIRED=1 in .env (the default) and restart the app to require accounts." })];
    }

    const $ = (s) => root.querySelector(s);
    function render() {
      const keep = root.querySelector(".af-keep");
      root.replaceChildren(...(keep ? [keep] : []),
        ...(!session.required ? offView() : session.signed_in ? inView() : formView()));
    }
    listeners.push(render);
    render();
    return { focus: () => { const e = $(`#${uid}-email`); if (e) e.focus({ preventScroll: true }); },
             showCreate: () => { mode = "create"; render(); } };
  }

  return { mount, refresh, signOut, onChange: (fn) => listeners.push(fn), get session() { return session; } };
})();
