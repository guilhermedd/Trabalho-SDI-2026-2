"use strict";

const params = new URLSearchParams(location.search);
const API_BASE = (params.get("api")
  || (["5500", "5501", "5502"].includes(location.port) ? "http://localhost:8000" : "")
).replace(/\/+$/, "");

const MAX_RANDOM = 100;
const ID_RE = /^\d{1,18}$/;

const logEl = document.getElementById("log");
const formAdd = document.getElementById("formAdd");
const formRandom = document.getElementById("formRandom");
const formKill = document.getElementById("formKill");
const formAuto = document.getElementById("formAuto");
const autoError = document.getElementById("autoError");
const btnAutoStart = document.getElementById("btnAutoStart");

// ---------- helpers ----------
function normalizeId(value) {
  return String(value).trim().replace(/^0+(?=\d)/, "");
}

function isValidServerId(id) {
  return ID_RE.test(id);
}

function isValidUserId(id) {
  return ID_RE.test(id) && id !== "0";
}

function sanitizeName(value) {
  return value.replace(/_/g, " ").replace(/\p{C}/gu, " ").replace(/\s+/g, " ").trim();
}

function setInvalid(input, invalid) {
  input.classList.toggle("is-invalid", invalid);
}

function setBusy(form, busy) {
  form.querySelectorAll("input, button").forEach(el => { el.disabled = busy; });
}

function addLog(message, ok, detail = "") {
  if (logEl.querySelector(".log-empty")) logEl.innerHTML = "";

  const li = document.createElement("li");
  li.className = "list-group-item";
  li.innerHTML = `
    <span class="log-time"></span>
    <span class="${ok ? "log-ok" : "log-err"}"></span>
    <span class="log-msg"></span>
    <div class="text-muted small"></div>
  `;
  li.querySelector(".log-time").textContent = new Date().toLocaleTimeString("pt-BR");
  li.querySelector(".log-ok, .log-err").textContent = ok ? "[OK]" : "[ERRO]";
  li.querySelector(".log-msg").textContent = message;
  li.querySelector(".text-muted").textContent = detail;

  logEl.prepend(li);
}

// POST JSON na API. Nunca lança exceção: devolve { ok, sent?, reply?, error? }.
async function postJSON(path, body) {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      return { ok: false, sent: data.sent, error: data.error || `HTTP ${res.status}` };
    }
    return data;
  } catch (err) {
    return { ok: false, error: `Falha de conexão com o Cadastro: ${err.message}` };
  }
}

function describe(result) {
  if (!result.ok) return result.error;
  return result.reply ? `Resposta: ${result.reply}` : "Enviado (sem resposta do coordenador)";
}

// ---------- adicionar usuário ----------
formAdd.addEventListener("submit", async (e) => {
  e.preventDefault();
  const idInput = document.getElementById("idAdd");
  const nomeInput = document.getElementById("nomeAdd");

  const id = normalizeId(idInput.value);
  const nome = sanitizeName(nomeInput.value);

  const idOk = isValidUserId(id);
  setInvalid(idInput, !idOk);
  if (!idOk) return;

  // Os dois formatos do enunciado: NEWUSER_<ID>_<NOME> ou NEWUSER_<ID>.
  const message = nome ? `NEWUSER_${id}_${nome}` : `NEWUSER_${id}`;
  setBusy(formAdd, true);
  try {
    const result = await postJSON("/api/message", { message });
    addLog(result.sent || message, result.ok, describe(result));
    if (result.ok) {
      formAdd.reset();
    }
  } finally {
    setBusy(formAdd, false);
    idInput.focus();
    refreshStatus();
  }
});

// ---------- usuários aleatórios ----------
formRandom.addEventListener("submit", async (e) => {
  e.preventDefault();
  const qtdInput = document.getElementById("qtdRandom");
  const qtd = Number(qtdInput.value);

  const ok = Number.isInteger(qtd) && qtd >= 1 && qtd <= MAX_RANDOM;
  setInvalid(qtdInput, !ok);
  if (!ok) return;

  setBusy(formRandom, true);
  try {
    for (let i = 0; i < qtd; i++) {
      const result = await postJSON("/api/random", {});
      addLog(result.sent || "NEWUSER (aleatório)", result.ok, describe(result));

      if (!result.ok && !String(result.error || "").startsWith("ERROR_")) {
        if (i + 1 < qtd) {
          addLog(`Geração interrompida após ${i + 1} de ${qtd} envios`, false,
                 "Verifique se o Cadastro e o coordenador estão no ar.");
        }
        break;
      }
    }
  } finally {
    setBusy(formRandom, false);
    refreshStatus();
  }
});

// ---------- matar servidor ----------
formKill.addEventListener("submit", async (e) => {
  e.preventDefault();
  const idInput = document.getElementById("idKill");
  const id = normalizeId(idInput.value);

  const ok = isValidServerId(id); 
  setInvalid(idInput, !ok);
  if (!ok) return;

  const message = `KILL_${id}`;
  setBusy(formKill, true);
  try {
    const result = await postJSON("/api/message", { message });
    addLog(result.sent || message, result.ok, describe(result));
    if (result.ok) formKill.reset();
  } finally {
    setBusy(formKill, false);
    idInput.focus();
    refreshStatus();
  }
});

document.getElementById("btnKillRandom").addEventListener("click", async () => {
  setBusy(formKill, true);
  try {
    // O Cadastro sorteia um ID da lista de servidores e envia o KILL.
    const result = await postJSON("/api/kill-random", {});
    addLog(result.sent || "KILL (aleatório)", result.ok, describe(result));
  } finally {
    setBusy(formKill, false);
    refreshStatus();
  }
});

// ---------- limpar log ----------
document.getElementById("clearLog").addEventListener("click", () => {
  logEl.innerHTML = `<li class="list-group-item text-muted small log-empty">Nenhuma mensagem enviada ainda.</li>`;
});

// Remove o estado de inválido ao digitar
document.querySelectorAll("input").forEach(input => {
  input.addEventListener("input", () => setInvalid(input, false));
});

// ---------- envio automático ----------
formAuto.addEventListener("submit", async (e) => {
  e.preventDefault();
  autoError.textContent = "";

  const t0 = Number(document.getElementById("autoT0").value);
  const r = Number(document.getElementById("autoR").value);
  const tmin = Number(document.getElementById("autoTmin").value);
  const limit = Number(document.getElementById("autoLimit").value.trim() || "0");

  const valid = [t0, r, tmin].every(Number.isFinite) && Number.isInteger(limit)
    && t0 > 0 && r > 0 && r < 1 && tmin > 0 && tmin <= t0 && limit >= 0;
  if (!valid) {
    autoError.textContent = "Requisitos: T0 > 0, 0 < r < 1, 0 < Tmin ≤ T0, limite inteiro ≥ 0.";
    return;
  }

  btnAutoStart.disabled = true;
  const data = await postJSON("/api/auto", { action: "start", t0, r, tmin, limit });
  if (!data.ok) {
    autoError.textContent = data.error || "Erro ao iniciar.";
    btnAutoStart.disabled = false;
  }
  refreshStatus();
});

document.getElementById("btnAutoStop").addEventListener("click", async () => {
  const data = await postJSON("/api/auto", { action: "stop" });
  autoError.textContent = data.ok ? "" : (data.error || "Erro ao parar.");
  refreshStatus();
});

// ---------- status ----------

let refreshing = false;
let lastListsKey = "";

function renderLists(servers, users) {
  const key = JSON.stringify([servers, users]);
  if (key === lastListsKey) return; // nada mudou: não redesenha
  lastListsKey = key;

  document.getElementById("countServers").textContent = servers.length;
  const srvBox = document.getElementById("listServers");
  srvBox.replaceChildren();
  if (!servers.length) {
    const span = document.createElement("span");
    span.className = "text-muted small";
    span.textContent = "nenhum";
    srvBox.append(span);
  }
  for (const id of servers) {
    const badge = document.createElement("span");
    badge.className = "badge text-bg-primary";
    badge.textContent = `Servidor ${id}`;
    srvBox.append(badge);
  }

  document.getElementById("countUsers").textContent = users.length;
  const tbody = document.getElementById("listUsers");
  tbody.replaceChildren();
  if (!users.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 2;
    td.className = "text-muted small";
    td.textContent = "nenhum";
    tr.append(td);
    tbody.append(tr);
  }
  for (const u of users) {
    const tr = document.createElement("tr");
    const tdId = document.createElement("td");
    const tdNome = document.createElement("td");
    tdId.textContent = u.id;
    tdNome.textContent = u.nome ?? "(sem nome)";
    if (u.nome == null) tdNome.className = "text-muted";
    tr.append(tdId, tdNome);
    tbody.append(tr);
  }
}

async function refreshStatus() {
  if (refreshing) return;
  refreshing = true;
  try {
    const res = await fetch(`${API_BASE}/api/status`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const st = await res.json();

    document.getElementById("stCoord").textContent = st.coordinator;
    document.getElementById("stServers").textContent = st.servers.length
      ? st.servers.join(", ")
      : "nenhum";
    document.getElementById("stUsers").textContent = st.users_sent;
    renderLists(st.servers, st.users);

    const a = st.auto;
    let txt;
    if (a.running && a.stopping) {
      txt = `parando... (${a.sent} enviados)`;
    } else if (a.running) {
      txt = `rodando, ${a.sent} enviados`;
      if (typeof a.next_interval === "number") {
        txt += `, intervalo atual ${a.next_interval.toFixed(2)} s`;
      }
    } else {
      txt = `parado (${a.sent} enviados)`;
    }
    if (a.failed) txt += `, ${a.failed} falhas`;
    if (a.last_error) txt += ` | último erro: ${a.last_error}`;
    document.getElementById("stAuto").textContent = txt;

    btnAutoStart.disabled = a.running;
  } catch (err) {
    document.getElementById("stCoord").textContent = location.protocol === "file:"
      ? "abra o painel por http://localhost:8000"
      : "sem conexão com o Cadastro";
    btnAutoStart.disabled = false;
  } finally {
    refreshing = false;
  }
}

refreshStatus();
setInterval(refreshStatus, 2000);