(function () {
  const jdTextSection = document.getElementById("jdTextSection");
  const jdPdfSection = document.getElementById("jdPdfSection");
  const analyzeBtn = document.getElementById("analyzeBtn");
  const statusText = document.getElementById("statusText");
  const resultsSection = document.getElementById("resultsSection");

  const statusValue = document.getElementById("statusValue");
  const skillsSourceValue = document.getElementById("skillsSourceValue");
  const recommendationValue = document.getElementById("recommendationValue");
  const decisionReasonValue = document.getElementById("decisionReasonValue");
  const reasonRow = document.getElementById("reasonRow");
  const confidenceValue = document.getElementById("confidenceValue");
  const scoreValue = document.getElementById("scoreValue");
  const scoreBarWrap = document.getElementById("scoreBarWrap");
  const scoreBarFill = document.getElementById("scoreBarFill");
  const matchedList = document.getElementById("matchedList");
  const gapsList = document.getElementById("gapsList");
  const verificationList = document.getElementById("verificationList");
  const messagesList = document.getElementById("messagesList");
  const devLogsSection = document.getElementById("devLogsSection");
  const devTokenInput = document.getElementById("devTokenInput");
  const devLogsOutput = document.getElementById("devLogsOutput");
  const refreshLogsBtn = document.getElementById("refreshLogsBtn");

  // Recommendation → color class mapping
  const REC_COLORS = {
    apply_now: "rec-green",
    apply_with_caution: "rec-yellow",
    upskill_first: "rec-orange",
    high_risk: "rec-red",
    insufficient_data: "rec-gray",
  };

  const CONF_COLORS = {
    high: "conf-green",
    medium: "conf-yellow",
    low: "conf-red",
    failed: "conf-red",
  };

  function setSourceVisibility() {
    const source = document.querySelector('input[name="jd_source"]:checked').value;
    jdTextSection.style.display = source === "text" ? "block" : "none";
    jdPdfSection.style.display = source === "pdf" ? "block" : "none";
  }

  function asSkillArray(raw) {
    return raw.split(",").map((x) => x.trim()).filter(Boolean);
  }

  function setList(el, values, renderItem) {
    el.innerHTML = "";
    if (!values || values.length === 0) {
      const li = document.createElement("li");
      li.textContent = "-";
      el.appendChild(li);
      return;
    }
    values.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = renderItem ? renderItem(item) : String(item);
      el.appendChild(li);
    });
  }

  function renderResult(data) {
    const payload = data.payload || {};

    // Show results section
    resultsSection.style.display = "block";

    // Status
    statusValue.textContent = data.status || "-";

    // Skills source
    skillsSourceValue.textContent = payload.skills_source || "-";

    // Recommendation with color
    const rec = payload.recommendation || "-";
    recommendationValue.textContent = rec;
    recommendationValue.className = "outcome-value recommendation-badge " + (REC_COLORS[rec] || "");

    // Decision reason — show if present
    if (payload.decision_reason) {
      decisionReasonValue.textContent = payload.decision_reason;
      reasonRow.style.display = "flex";
    } else {
      reasonRow.style.display = "none";
    }

    // Confidence with color
    const conf = payload.confidence || "-";
    confidenceValue.textContent = conf;
    confidenceValue.className = "outcome-value " + (CONF_COLORS[conf] || "");

    // Score + bar
    if (payload.match_score == null) {
      scoreValue.textContent = "-";
      scoreBarWrap.style.display = "none";
    } else {
      scoreValue.textContent = payload.match_score + "%";
      scoreBarWrap.style.display = "block";
      scoreBarFill.style.width = payload.match_score + "%";
      // Color the bar based on score
      if (payload.match_score >= 80) {
        scoreBarFill.className = "score-bar-fill bar-green";
      } else if (payload.match_score >= 60) {
        scoreBarFill.className = "score-bar-fill bar-yellow";
      } else {
        scoreBarFill.className = "score-bar-fill bar-red";
      }
    }

    setList(matchedList, payload.matched_skills || []);
    setList(gapsList, payload.skill_gaps || []);
    setList(verificationList, payload.verification || [], (v) => {
      const flag = v.verified ? "✓" : "✗";
      return `${flag}  ${v.field}  |  ${v.evidence_quote || "no evidence"}`;
    });

    const messages = [];
    (data.warnings || []).forEach((w) => messages.push(`WARN | ${w.code} | ${w.message}`));
    (data.errors || []).forEach((e) => messages.push(`ERROR | ${e.code} | ${e.message}`));
    setList(messagesList, messages);
  }

  function setBusy(isBusy, text) {
    analyzeBtn.disabled = isBusy;
    statusText.textContent = text || "";
  }

  function isDevMode() {
    const params = new URLSearchParams(window.location.search);
    return params.get("dev") === "1";
  }

  let pollingInterval = null;

  async function validateAndLoadLogs() {
    if (!isDevMode()) return;
    const token = (devTokenInput.value || "").trim();
    if (!token) {
      devLogsOutput.textContent = "Enter DEV_LOG_TOKEN and click Load Logs to start polling.";
      return;
    }
    await refreshDevLogs(token);
    if (!pollingInterval) {
      pollingInterval = setInterval(() => refreshDevLogs(token), 15000);
    }
  }

  async function refreshDevLogs(token) {
    if (!isDevMode()) return;
    if (!token) token = (devTokenInput.value || "").trim();
    if (token) localStorage.setItem("devLogToken", token);
    const headers = {};
    if (token) headers["X-Dev-Log-Token"] = token;
    try {
      const response = await fetch("/dev/logs?tail=250", { headers });
      const body = await response.json();
      if (!response.ok) {
        devLogsOutput.textContent = `Failed to load logs: ${body.detail || response.statusText}`;
        if (response.status === 403 && pollingInterval) {
          clearInterval(pollingInterval);
          pollingInterval = null;
          localStorage.removeItem("devLogToken");
        }
        return;
      }
      devLogsOutput.textContent = (body.lines || []).join("\n");
    } catch (err) {
      devLogsOutput.textContent = `Failed to load logs: ${String(err)}`;
    }
  }

  async function sendAnalyzeRequest() {
    const source = document.querySelector('input[name="jd_source"]:checked').value;
    const jdText = document.getElementById("jdText").value.trim();
    const jdPdf = document.getElementById("jdPdf").files[0];
    const resumePdf = document.getElementById("resumePdf").files[0];
    const skills = asSkillArray(document.getElementById("skillsInput").value);

    if (source === "text" && !jdText) { alert("Please paste JD text."); return; }
    if (source === "pdf" && !jdPdf) { alert("Please upload a JD PDF."); return; }

    setBusy(true, "Analyzing...");
    try {
      let response;
      if (source === "text" && !resumePdf) {
        response = await fetch("/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ jd_text: jdText, skills: skills }),
        });
      } else {
        const form = new FormData();
        if (jdText) form.append("jd_text", jdText);
        if (jdPdf) form.append("jd_pdf", jdPdf);
        if (resumePdf) form.append("resume_pdf", resumePdf);
        form.append("skills", JSON.stringify(skills));
        response = await fetch("/analyze-with-resume", { method: "POST", body: form });
      }

      const body = await response.json();
      renderResult(body);
      statusText.textContent = response.ok ? "Done." : "Request completed with errors.";
    } catch (err) {
      setList(messagesList, [`ERROR | CLIENT | ${String(err)}`]);
      statusValue.textContent = "error";
      statusText.textContent = "Request failed.";
      resultsSection.style.display = "block";
    } finally {
      setBusy(false);
    }
  }

  document.querySelectorAll('input[name="jd_source"]').forEach((radio) => {
    radio.addEventListener("change", setSourceVisibility);
  });
  analyzeBtn.addEventListener("click", sendAnalyzeRequest);

  if (isDevMode() && devLogsSection) {
    devLogsSection.style.display = "block";
    devTokenInput.value = localStorage.getItem("devLogToken") || "";
    if (refreshLogsBtn) refreshLogsBtn.addEventListener("click", validateAndLoadLogs);
    if (devTokenInput.value.trim()) {
      validateAndLoadLogs();
    } else {
      if (devLogsOutput) devLogsOutput.textContent = "Enter DEV_LOG_TOKEN and click Load Logs to start polling.";
    }
  }

  setSourceVisibility();
})();
