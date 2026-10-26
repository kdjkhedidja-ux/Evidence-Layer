(() => {
  const params = new URLSearchParams(window.location.hash.slice(1));
  window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);

  const accessToken = params.get("access_token");
  const refreshToken = params.get("refresh_token");
  const valid = params.get("type") === "recovery" &&
    params.getAll("access_token").length === 1 &&
    params.getAll("refresh_token").length === 1 &&
    accessToken && accessToken.length <= 8192 &&
    refreshToken && refreshToken.length <= 8192;
  if (!valid) {
    window.location.replace("/auth/error?reason=invalid-link");
    return;
  }

  fetch("/api/auth/recovery-session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accessToken, refreshToken }),
    credentials: "same-origin",
    cache: "no-store",
    referrerPolicy: "no-referrer",
  }).then((response) => {
    window.location.replace(response.ok ? "/reset-password" : "/auth/error?reason=invalid-link");
  }).catch(() => {
    window.location.replace("/auth/error?reason=invalid-link");
  });
})();
