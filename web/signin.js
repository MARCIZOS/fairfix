/* Continue to the dashboard after signing in or creating an account. */
"use strict";
const card = FFAuth.mount(document.getElementById("si-card"), {
  afterSignIn: () => { location.href = "/dashboard.html"; },
});
if (new URLSearchParams(location.search).get("mode") === "create") card.showCreate();
FFAuth.refresh().then((session) => {
  if (!session.required || session.signed_in) location.replace("/dashboard.html");
  else card.focus();
});
