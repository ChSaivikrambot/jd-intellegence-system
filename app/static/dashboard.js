(function () {
  const jdTextSection = document.getElementById("jdTextSection");
  const jdPdfSection = document.getElementById("jdPdfSection");
  const analyzeBtn = document.getElementById("analyzeBtn");
  const statusText = document.getElementById("statusText");

  const statusValue = document.getElementById("statusValue");
  const skillsSourceValue = document.getElementById("skillsSourceValue");
  const recommendationValue = document.getElementById("recommendationValue");
  const confidenceValue = document.getElementById("confidenceValue");
  const scoreValue = document.getElementById("scoreValue");
  const matchedList = document.getElementById("matchedList");
  const gapsList = document.getElementById("gapsList");
  const verificationList = document.getElementById("verificationList");
  const messagesList = document.getElementById("messagesList");
  const devLogsSection = document.getElementById("devLogsSection");
  const devTokenInput = document.getElementById("devTokenInput");
  const devLogsOutput = document.getElementById("devLogsOutput");
  const refreshLogsBtn = document.getElementById("refreshLogsBtn");

  function setSourceVisibility() {
    const source = document.querySelector('input[name="jd_source"]:checked').value;
    jdTextSection.style.display = source === "text" ? "block" : "none";
    jdPdfSection.style.display = source === "pdf" ? "block" : "none";
  }

  function asSkillArray(raw) {
    return raw
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
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
    statusValue.textContent = data.status || "-";
    skillsSourceValue.textContent = payload.skills_source || "-";
    recommendationValue.textContent = payload.recommendation || "-";
    confidenceValue.textContent = payload.confidence || "-";
    scoreValue.textContent = payload.match_score == null ? "-" : payload.match_score + "%";

    setList(matchedList, payload.matched_skills || []);
    setList(gapsList, payload.skill_gaps || []);
    setList(verificationList, payload.verification || [], (v) => {
      const flag = v.verified ? "PASS" : "FAIL";
      return `${flag} | ${v.field} | ${v.evidence_quote || "no evidence"}`;
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

  async function refreshDevLogs() {
    if (!isDevMode()) return;
    const token = (devTokenInput.value || "").trim();
    if (token) localStorage.setItem("devLogToken", token);
    const headers = {};
    if (token) headers["X-Dev-Log-Token"] = token;
    try {
      const response = await fetch("/dev/logs?tail=250", { headers });
      const body = await response.json();
      if (!response.ok) {
        devLogsOutput.textContent = `Failed to load logs: ${body.detail || response.statusText}`;
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

    if (source === "text" && !jdText) {
      alert("Please paste JD text.");
      return;
    }
    if (source === "pdf" && !jdPdf) {
      alert("Please upload a JD PDF.");
      return;
    }

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
        // Future-ready path for JD PDF and/or resume mode.
        // Backend can expose multipart endpoint(s) for these combinations.
        const form = new FormData();
        if (jdText) form.append("jd_text", jdText);
        if (jdPdf) form.append("jd_pdf", jdPdf);
        if (resumePdf) form.append("resume_pdf", resumePdf);
        form.append("skills", JSON.stringify(skills));
        response = await fetch("/analyze-with-resume", { method: "POST", body: form });
      }

      const body = await response.json();
      renderResult(body);

      if (!response.ok) {
        statusText.textContent = "Request completed with errors.";
      } else {
        statusText.textContent = "Done.";
      }
    } catch (err) {
      setList(messagesList, [`ERROR | CLIENT | ${String(err)}`]);
      statusValue.textContent = "error";
      statusText.textContent = "Request failed.";
    } finally {
      setBusy(false);
    }
  }

  document.querySelectorAll('input[name="jd_source"]').forEach((radio) => {
    radio.addEventListener("change", setSourceVisibility);
  });
  analyzeBtn.addEventListener("click", sendAnalyzeRequest);
  if (isDevMode()) {
    devLogsSection.style.display = "block";
    devTokenInput.value = localStorage.getItem("devLogToken") || "";
    refreshLogsBtn.addEventListener("click", refreshDevLogs);
    refreshDevLogs();
    setInterval(refreshDevLogs, 3000);
  }
  setSourceVisibility();
})();
