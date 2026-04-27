const state = {
  threadId: null,
  session: null,
  busy: false,
};

const el = {
  threadLabel: document.getElementById("threadLabel"),
  sessionStatus: document.getElementById("sessionStatus"),
  projectStatus: document.getElementById("projectStatus"),
  createSessionBtn: document.getElementById("createSessionBtn"),
  validateBtn: document.getElementById("validateBtn"),
  exportBtn: document.getElementById("exportBtn"),
  projectTypeSelect: document.getElementById("projectTypeSelect"),
  autoConfirmInput: document.getElementById("autoConfirmInput"),
  messages: document.getElementById("messages"),
  messageForm: document.getElementById("messageForm"),
  messageInput: document.getElementById("messageInput"),
  sendMessageBtn: document.getElementById("sendMessageBtn"),
  planBtn: document.getElementById("planBtn"),
  templateList: document.getElementById("templateList"),
  candidateCount: document.getElementById("candidateCount"),
  projectId: document.getElementById("projectId"),
  versionId: document.getElementById("versionId"),
  projectPath: document.getElementById("projectPath"),
  stateSummary: document.getElementById("stateSummary"),
  patchInput: document.getElementById("patchInput"),
  patchResult: document.getElementById("patchResult"),
  applyPatchBtn: document.getElementById("applyPatchBtn"),
  formatPatchBtn: document.getElementById("formatPatchBtn"),
  validationResult: document.getElementById("validationResult"),
  toast: document.getElementById("toast"),
};

el.createSessionBtn.addEventListener("click", createSession);
el.messageForm.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});
el.planBtn.addEventListener("click", planPatch);
el.applyPatchBtn.addEventListener("click", applyPatch);
el.formatPatchBtn.addEventListener("click", formatPatch);
el.validateBtn.addEventListener("click", validateProject);
el.exportBtn.addEventListener("click", exportProject);

document.querySelectorAll(".tab-btn").forEach((button) => {
  button.addEventListener("click", () => setTab(button.dataset.tab));
});

render();

async function createSession() {
  await runTask(async () => {
    const body = {
      project_type: el.projectTypeSelect.value || null,
      auto_confirm_template: el.autoConfirmInput.checked,
    };
    const data = await api("/api/sessions", { method: "POST", body });
    state.threadId = data.thread_id;
    state.session = data.state;
    render();
    showToast("会话已创建", "ok");
  });
}

async function sendMessage(options = {}) {
  if (!state.threadId) {
    showToast("请先新建会话", "warn");
    return;
  }
  const message = options.message || el.messageInput.value.trim();
  if (!message) {
    showToast("请输入内容", "warn");
    return;
  }

  await runTask(async () => {
    const body = {
      message,
      project_type: el.projectTypeSelect.value || undefined,
      auto_confirm_template: el.autoConfirmInput.checked,
      selected_template_id: options.selectedTemplateId,
      pending_patch: options.pendingPatch,
    };
    const data = await api(`/api/sessions/${state.threadId}/message`, { method: "POST", body });
    state.session = data.state;
    if (!options.keepInput) {
      el.messageInput.value = "";
    }
    render();
    showToast("工作流已返回", "ok");
  });
}

async function planPatch() {
  if (!state.session?.current_project_path) {
    showToast("当前没有工程版本", "warn");
    return;
  }
  const message = el.messageInput.value.trim();
  if (!message) {
    showToast("请输入修改指令", "warn");
    return;
  }

  await runTask(async () => {
    const body = {
      message,
      project_path: state.session.current_project_path,
      template_id: state.session.selected_template_id,
      project_type: state.session.project_type,
    };
    const data = await api("/api/planner/plan", { method: "POST", body });
    el.patchResult.textContent = pretty(data);
    if (data.pending_patch) {
      el.patchInput.value = pretty(data.pending_patch);
      setTab("patch");
    }
    showToast(data.status === "planned" ? "补丁已规划" : "需要补充信息", data.status === "planned" ? "ok" : "warn");
  });
}

async function applyPatch() {
  if (!state.threadId) {
    showToast("请先新建会话", "warn");
    return;
  }
  let patch;
  try {
    patch = JSON.parse(el.patchInput.value);
  } catch (error) {
    showToast(`补丁 JSON 无效：${error.message}`, "bad");
    return;
  }

  await sendMessage({
    message: "应用结构化补丁",
    pendingPatch: patch,
    keepInput: true,
  });
  setTab("patch");
}

function formatPatch() {
  try {
    el.patchInput.value = pretty(JSON.parse(el.patchInput.value));
  } catch (error) {
    showToast(`补丁 JSON 无效：${error.message}`, "bad");
  }
}

async function validateProject() {
  if (!state.session?.current_project_id) {
    showToast("当前没有项目", "warn");
    return;
  }
  await runTask(async () => {
    const data = await api(`/api/projects/${state.session.current_project_id}/validate`, { method: "POST" });
    el.validationResult.textContent = pretty(data);
    setTab("validation");
    showToast(data.valid ? "校验通过" : "校验失败", data.valid ? "ok" : "bad");
  });
}

function exportProject() {
  if (!state.session?.current_project_id) {
    showToast("当前没有项目", "warn");
    return;
  }
  window.location.href = `/api/projects/${state.session.current_project_id}/export`;
}

async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  return response.json();
}

async function runTask(task) {
  if (state.busy) {
    return;
  }
  state.busy = true;
  renderButtons();
  try {
    await task();
  } catch (error) {
    showToast(error.message, "bad");
  } finally {
    state.busy = false;
    renderButtons();
  }
}

function render() {
  const session = state.session;
  el.threadLabel.textContent = state.threadId ? `thread_id: ${state.threadId}` : "未创建会话";
  el.sessionStatus.textContent = session?.status || "idle";
  el.projectStatus.textContent = session?.current_project_id ? session.status || "ready" : "none";
  el.projectId.textContent = session?.current_project_id || "-";
  el.versionId.textContent = session?.current_project_version_id || "-";
  el.projectPath.textContent = session?.current_project_path || "-";
  el.stateSummary.textContent = pretty(compactState(session));
  el.patchResult.textContent = pretty(session?.patch_result || session?.planner_result || {});
  el.validationResult.textContent = pretty(session?.validation_report || {});
  renderMessages(session?.messages || []);
  renderTemplates(session?.template_candidates || []);
  renderButtons();
}

function renderButtons() {
  const hasThread = Boolean(state.threadId);
  const hasProject = Boolean(state.session?.current_project_id);
  el.createSessionBtn.disabled = state.busy;
  el.sendMessageBtn.disabled = state.busy || !hasThread;
  el.planBtn.disabled = state.busy || !state.session?.current_project_path;
  el.applyPatchBtn.disabled = state.busy || !hasThread;
  el.validateBtn.disabled = state.busy || !hasProject;
  el.exportBtn.disabled = state.busy || !hasProject;
}

function renderMessages(messages) {
  el.messages.innerHTML = "";
  if (!messages.length) {
    el.messages.appendChild(emptyBlock("暂无消息"));
    return;
  }
  for (const message of messages) {
    const item = document.createElement("div");
    item.className = `message ${message.role || ""}`;
    const role = document.createElement("span");
    role.className = "message-role";
    role.textContent = message.role || "message";
    item.appendChild(role);
    item.append(document.createTextNode(message.content || ""));
    el.messages.appendChild(item);
  }
  el.messages.scrollTop = el.messages.scrollHeight;
}

function renderTemplates(candidates) {
  el.templateList.innerHTML = "";
  el.candidateCount.textContent = String(candidates.length);
  if (!candidates.length) {
    el.templateList.appendChild(emptyBlock("暂无候选模板"));
    return;
  }
  candidates.forEach((candidate, index) => {
    const item = document.createElement("article");
    item.className = "template-item";

    const title = document.createElement("h3");
    title.textContent = `${index + 1}. ${candidate.file_name}`;
    item.appendChild(title);

    const summary = document.createElement("p");
    summary.textContent = candidate.summary || "";
    item.appendChild(summary);

    const meta = document.createElement("div");
    meta.className = "template-meta";
    meta.append(tag(candidate.project_type_label || candidate.project_type || "-"));
    meta.append(tag(`score ${candidate.score ?? "-"}`));
    meta.append(tag(`${candidate.node_count ?? 0} 节点`));
    item.appendChild(meta);

    if (candidate.reasons?.length) {
      const reasons = document.createElement("ul");
      reasons.className = "reason-list";
      candidate.reasons.slice(0, 4).forEach((reason) => {
        const li = document.createElement("li");
        li.textContent = reason;
        reasons.appendChild(li);
      });
      item.appendChild(reasons);
    }

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "使用此模板";
    button.addEventListener("click", () => {
      sendMessage({
        message: `使用模板 ${candidate.file_name}`,
        selectedTemplateId: candidate.template_id,
      });
    });
    item.appendChild(button);
    el.templateList.appendChild(item);
  });
}

function compactState(session) {
  if (!session) {
    return {};
  }
  return {
    project_type: session.project_type,
    selected_template_id: session.selected_template_id,
    status: session.status,
    next_action: session.next_action,
    current_project_id: session.current_project_id,
    current_project_version_id: session.current_project_version_id,
    current_project_path: session.current_project_path,
    error: session.error,
  };
}

function setTab(name) {
  document.querySelectorAll(".tab-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  document.querySelectorAll(".tab-page").forEach((page) => {
    page.classList.remove("active");
  });
  document.getElementById(`${name}Tab`).classList.add("active");
}

function pretty(value) {
  return JSON.stringify(value ?? {}, null, 2);
}

function tag(text) {
  const item = document.createElement("span");
  item.className = "tag";
  item.textContent = text;
  return item;
}

function emptyBlock(text) {
  const item = document.createElement("div");
  item.className = "message";
  item.textContent = text;
  return item;
}

function showToast(message, type = "ok") {
  el.toast.textContent = message;
  el.toast.className = `toast show ${type}`;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    el.toast.className = "toast";
  }, 2600);
}
