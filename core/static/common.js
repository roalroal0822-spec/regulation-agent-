// 세 앱 공통 화면 도우미 (외부 라이브러리 없음)
"use strict";

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return e;
}
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
// '__밑줄__', '**굵게**' 표기를 HTML 로
function rich(s) {
  return esc(s).replace(/__(.+?)__/g, "<u>$1</u>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
}

function toast(msg, err) {
  const t = el("div", { class: "toast" + (err ? " err" : "") }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), err ? 6000 : 2800);
}

async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) {
    const m = (data && data.error) || `요청 실패 (${r.status})`;
    toast(m, true);
    throw new Error(m);
  }
  return data;
}

// 파일 저장: POST 로 산출물을 받아 내려받기
async function download(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  if (!r.ok) {
    let m = "내려받기 실패";
    try { m = (await r.json()).error || m; } catch (e) {}
    toast(m, true); return;
  }
  const cd = r.headers.get("Content-Disposition") || "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)/);
  const name = m ? decodeURIComponent(m[1]) : "download";
  const blob = await r.blob();
  const a = el("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove();
  toast(`${name} 저장됨`);
}

function exportButtons(url, bodyFn) {
  return el("span", { class: "row" },
    el("button", { onclick: () => download(url + "?fmt=hwpx", bodyFn ? bodyFn() : {}) }, "한글(HWPX)"),
    el("button", { onclick: () => download(url + "?fmt=docx", bodyFn ? bodyFn() : {}) }, "워드(DOCX)"),
    el("button", { onclick: () => download(url + "?fmt=md", bodyFn ? bodyFn() : {}) }, "마크다운"));
}

// 파일/폴더 → [{path, b64}]
async function readFiles(fileList) {
  const out = [];
  for (const f of fileList) {
    const buf = await f.arrayBuffer();
    let bin = "";
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    out.push({ path: f.webkitRelativePath || f._relPath || f.name, b64: btoa(bin) });
  }
  return out;
}

// 끌어다 놓기(폴더 포함) 지원
async function filesFromDrop(ev) {
  const items = [...(ev.dataTransfer.items || [])];
  const files = [];
  async function walk(entry, prefix) {
    if (entry.isFile) {
      await new Promise(res => entry.file(f => { f._relPath = prefix + f.name; files.push(f); res(); }, res));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise(res => reader.readEntries(res, () => res([])));
        for (const e of batch) await walk(e, prefix + entry.name + "/");
      } while (batch.length);
    }
  }
  const entries = items.map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (entries.length) { for (const e of entries) await walk(e, ""); }
  else files.push(...ev.dataTransfer.files);
  return files;
}

function modal(title, ...content) {
  const bg = el("div", { class: "modal-bg", onclick: e => { if (e.target === bg) bg.remove(); } });
  const m = el("div", { class: "modal" },
    el("div", { class: "row" }, el("h3", { style: { flex: 1, margin: 0 } }, title),
      el("button", { onclick: () => bg.remove() }, "닫기")),
    el("div", { style: { marginTop: "10px" } }, ...content));
  bg.append(m); document.body.append(bg);
  return bg;
}

// 출처 표시 칩: 누르면 원문 위치와 앞뒤 문맥을 보여줌
function citeChips(sources, contextUrl) {
  if (!sources || !sources.length) return el("span", { class: "tag warn" }, "출처 없음");
  return el("span", {}, sources.map(s => el("span", {
    class: "chip", title: `${s.file} · ${s.loc}\n${s.quote || ""}`,
    onclick: () => showSource(s, contextUrl)
  }, `${shortName(s.file)} · ${s.loc}`)));
}
function shortName(p) { const n = String(p || "").split("/").pop(); return n.length > 22 ? n.slice(0, 20) + "…" : n; }

async function showSource(s, contextUrl) {
  const body = el("div", {}, el("div", { class: "kv" }, el("div", {}, "파일"), el("div", {}, s.file),
    el("div", {}, "위치"), el("div", {}, s.loc)), el("div", { class: "quote hit" }, s.quote || ""));
  modal("출처 확인", body);
  if (contextUrl && s.block_id) {
    try {
      const ctx = await api("GET", `${contextUrl}?block=${encodeURIComponent(s.block_id)}`);
      const box = el("div", {}, el("h4", {}, "원문 앞뒤 내용"));
      for (const b of ctx.blocks) box.append(el("div", { class: "quote" + (b.id === s.block_id ? " hit" : "") }, `[${b.loc}] ${b.text}`));
      body.append(box);
    } catch (e) {}
  }
}

// 탭 전환
function setupTabs(onChange) {
  const btns = $$("nav.tabs button");
  function go(id) {
    btns.forEach(b => b.classList.toggle("on", b.dataset.tab === id));
    $$("section.tab").forEach(s => s.hidden = s.id !== "tab-" + id);
    try { localStorage.setItem(location.pathname + ":tab", id); } catch (e) {}
    onChange && onChange(id);
  }
  btns.forEach(b => b.addEventListener("click", () => go(b.dataset.tab)));
  let first = btns[0] && btns[0].dataset.tab;
  try { const saved = localStorage.getItem(location.pathname + ":tab"); if (saved && btns.some(b => b.dataset.tab === saved)) first = saved; } catch (e) {}
  if (first) go(first);
  return go;
}

// 모델 설정 화면 (헤더 배지 클릭)
async function setupLLMBadge() {
  const badge = $("#llm-badge");
  if (!badge) return;
  async function refresh() {
    const info = await api("GET", "/api/llm");
    badge.textContent = "모델: " + info.label;
    badge.classList.toggle("on", !!(info.config && info.config.provider && info.config.provider !== "none"));
    return info;
  }
  badge.addEventListener("click", async () => {
    const info = await refresh();
    const c = info.config;
    const prov = el("select", {}, ...[["none", "사용 안 함(규칙 기반)"], ["openai", "OpenAI 호환 API (vLLM·Ollama /v1·AI 공통기반 등)"], ["ollama", "Ollama 기본 API"]]
      .map(([v, t]) => el("option", { value: v, selected: c.provider === v }, t)));
    const base = el("input", { class: "wide", value: c.base_url || "" });
    const model = el("input", { class: "wide", value: c.model || "" });
    const keyenv = el("input", { class: "wide", value: c.api_key_env || "LLM_API_KEY" });
    const result = el("div", { class: "small muted" });
    modal("모델 연결 설정",
      el("p", { class: "small muted" }, "모델을 바꿔도 기능은 그대로입니다. 모델 없이도 규칙 기반으로 동작합니다. API 키는 화면에 입력하지 않고 환경변수로만 읽습니다."),
      el("label", { class: "f" }, "연결 방식"), prov,
      el("label", { class: "f" }, "주소 (예: http://localhost:11434/v1)"), base,
      el("label", { class: "f" }, "모델 이름 (예: gemma3:4b, exaone3.5:7.8b, llama3.1:8b, gpt-oss:20b)"), model,
      el("label", { class: "f" }, "API 키 환경변수 이름 (키가 필요한 경우)"), keyenv,
      el("div", { class: "row", style: { marginTop: "12px" } },
        el("button", { class: "primary", onclick: async () => {
          await api("POST", "/api/llm", { provider: prov.value, base_url: base.value, model: model.value, api_key_env: keyenv.value });
          await refresh(); toast("저장했습니다");
        } }, "저장"),
        el("button", { onclick: async () => {
          result.textContent = "확인 중…";
          const r = await api("POST", "/api/llm/test");
          result.textContent = (r.ok ? "연결 성공: " : "연결 실패: ") + r.label + " / " + (r.detail || "") + (r.seconds ? ` (${r.seconds}초)` : "");
        } }, "연결 확인")),
      result);
  });
  refresh();
}

function busy(btn, on, text) {
  if (!btn) return;
  if (on) { btn._t = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${text || "처리 중"}`; }
  else { btn.disabled = false; if (btn._t) btn.innerHTML = btn._t; }
}

document.addEventListener("DOMContentLoaded", setupLLMBadge);
