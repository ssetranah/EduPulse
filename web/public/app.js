// Phase 1: only probes the API health endpoint. No student data is requested or displayed.
(function () {
  "use strict";

  var statusEl = document.getElementById("api-status");
  var details = document.getElementById("api-details");

  function setStatus(kind, text) {
    statusEl.className = "status status--" + kind;
    statusEl.textContent = text;
  }

  fetch("api/healthz", { cache: "no-store" })
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (info) {
      setStatus("ok", "API reachable");
      document.getElementById("d-service").textContent = info.service;
      document.getElementById("d-version").textContent = info.version;
      document.getElementById("d-mode").textContent = info.data_mode;
      document.getElementById("d-env").textContent = info.environment;
      details.hidden = false;
    })
    .catch(function () {
      setStatus("error", "API unreachable");
    });
})();
