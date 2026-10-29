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
  const nav = $("nav.tabs");
  const ink = nav && el("span", { class: "tab-ink" });
  if (ink) { nav.prepend(ink); nav.classList.add("inked"); }
  const h1 = $("header.top h1");
  if (nav && h1) {
    nav.prepend(el("span", { class: "nav-title" }, h1.textContent));
    const stick = () => nav.classList.toggle("stuck", nav.getBoundingClientRect().top <= 0 && window.scrollY > 0);
    window.addEventListener("scroll", stick, { passive: true }); stick();
  }
  let cur = -1;
  function moveInk() {
    const b = btns.find(x => x.classList.contains("on"));
    if (ink && b) { ink.style.left = b.offsetLeft + "px"; ink.style.width = b.offsetWidth + "px"; }
  }
  window.addEventListener("resize", moveInk);
  function go(id) {
    const idx = btns.findIndex(b => b.dataset.tab === id);
    const dir = cur < 0 ? "" : idx > cur ? "from-right" : idx < cur ? "from-left" : "";
    cur = idx;
    btns.forEach(b => b.classList.toggle("on", b.dataset.tab === id));
    moveInk();
    if (btns[idx] && btns[idx].scrollIntoView) btns[idx].scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    $$("section.tab").forEach(s => {
      s.classList.remove("from-right", "from-left");
      s.hidden = s.id !== "tab-" + id;
      if (!s.hidden && dir) { void s.offsetWidth; s.classList.add(dir); }
    });
    if (nav && dir && window.scrollY > nav.offsetTop) window.scrollTo({ top: nav.offsetTop, behavior: "smooth" });
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
    const fams = info.families || [];
    const prov = el("select", {}, ...[["none", "사용 안 함(규칙 기반)"], ["openai", "OpenAI 호환 API (AI 공통기반·vLLM·Ollama /v1 등)"], ["ollama", "Ollama 기본 API"]]
      .map(([v, t]) => el("option", { value: v, selected: c.provider === v }, t)));
    const base = el("input", { class: "wide", value: c.base_url || "", placeholder: "예: http://localhost:11434/v1 또는 기관에서 안내받은 주소" });
    const dl = el("datalist", { id: "llm-models" });
    const model = el("input", { class: "wide", value: c.model || "", list: "llm-models", placeholder: "모델 이름" });
    const famNote = el("div", { class: "small muted", style: { marginTop: "6px" } });
    const keyenv = el("input", { class: "wide", value: c.api_key_env || "LLM_API_KEY" });
    const result = el("div", { class: "small muted", style: { marginTop: "10px" } });
    const famOf = m => { m = (m || "").toLowerCase(); const rx = { gemma: /gemma/, llama: /llama/, "gpt-oss": /gpt-oss|gpt_oss/, exaone: /exaone/, hyperclovax: /hyperclova|hcx/, solar: /solar/ };
      return fams.find(f => rx[f.key] && rx[f.key].test(m)); };
    const showFam = () => { const f = famOf(model.value); famNote.textContent = f ? `인식한 모델 계열: ${f.name} (${f.maker})` : (model.value ? "계열을 알 수 없는 모델도 OpenAI 호환이면 연결할 수 있습니다." : ""); };
    model.addEventListener("input", showFam); showFam();
    const preset = (p, url) => { prov.value = p; base.value = url; };
    const famChips = el("div", { class: "row", style: { gap: "6px" } }, ...fams.map(f =>
      el("span", { class: "chip", title: f.ollama.length ? "Ollama 예시: " + f.ollama.join(", ") : "OpenAI 호환 서버의 모델 이름을 입력하세요",
        onclick: () => {
          if (f.ollama.length) { model.value = f.ollama[0]; }
          else { model.value = ""; model.focus(); toast(`${f.name}은(는) 서버에 등록된 이름을 입력하거나 목록을 불러오세요`); }
          showFam();
        } }, f.name)));
    const loadModels = el("button", { onclick: async () => {
      loadModels.disabled = true;
      try {
        const r = await api("POST", "/api/llm/models", { provider: prov.value, base_url: base.value, api_key_env: keyenv.value });
        dl.replaceChildren(...r.models.map(m => el("option", { value: m })));
        result.textContent = r.models.length ? `서버 모델 ${r.models.length}개: ` + r.models.map(m => { const f = famOf(m); return f ? `${m} (${f.name})` : m; }).join(", ") : "서버에 등록된 모델이 없습니다.";
      } catch (e) { result.textContent = "목록을 받지 못했습니다: " + e.message; }
      loadModels.disabled = false;
    }, style: { flex: "none", whiteSpace: "nowrap" } }, "모델 목록 불러오기");
    modal("모델 연결 설정",
      el("p", { class: "small muted" }, "특정 모델에 묶이지 않습니다. 모델을 바꿔도 기능은 그대로이고, 모델 없이도 규칙 기반으로 동작합니다. API 키는 화면에 입력하지 않고 환경변수로만 읽습니다."),
      el("label", { class: "f" }, "빠른 설정"),
      el("div", { class: "row", style: { gap: "6px" } },
        el("button", { class: "small", onclick: () => preset("openai", "http://localhost:11434/v1") }, "내 PC의 Ollama"),
        el("button", { class: "small", onclick: () => { preset("openai", ""); base.focus(); } }, "행정안전부 AI 공통기반·vLLM (OpenAI 호환)"),
        el("button", { class: "small", onclick: () => preset("none", base.value) }, "모델 사용 안 함")),
      el("label", { class: "f" }, "연결 방식"), prov,
      el("label", { class: "f" }, "주소"), base,
      el("label", { class: "f" }, "모델 계열 (AI 공통기반 지원·지원 예정 모델)"), famChips,
      el("label", { class: "f" }, "모델 이름"), el("div", { class: "row", style: { flexWrap: "nowrap" } }, model, loadModels), dl, famNote,
      el("label", { class: "f" }, "API 키 환경변수 이름 (키가 필요한 경우)"), keyenv,
      el("div", { class: "row", style: { marginTop: "16px" } },
        el("button", { class: "primary", onclick: async () => {
          await api("POST", "/api/llm", { provider: prov.value, base_url: base.value, model: model.value, api_key_env: keyenv.value });
          await refresh(); toast("저장했습니다");
        } }, "저장"),
        el("button", { onclick: async () => {
          result.textContent = "확인 중…";
          const r = await api("POST", "/api/llm/test");
          result.textContent = (r.ok ? "연결 성공: " : "연결 실패: ") + r.label + (r.family ? ` [${r.family}]` : "") + " / " + (r.detail || "") + (r.seconds ? ` (${r.seconds}초)` : "");
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

// 스크롤하면 카드가 떠오르듯 나타남(새로 그려지는 카드 포함)
(function setupReveal() {
  if (!("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { rootMargin: "0px 0px -40px 0px" });
  const watch = root => (root.matches && root.matches("main .panel") ? [root] : [])
    .concat(root.querySelectorAll ? [...root.querySelectorAll("main .panel")] : [])
    .forEach(p => { if (!p.classList.contains("rv") && !p.closest(".modal-bg")) { p.classList.add("rv"); io.observe(p); } });
  document.addEventListener("DOMContentLoaded", () => {
    watch(document);
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => n.nodeType === 1 && watch(n))))
      .observe(document.body, { childList: true, subtree: true });
  });
})();

// ------------------------------------------------------------ 접근성: 본문 바로가기, 글자 크기
function setupA11y() {
  const main = $("main");
  if (main && !main.id) main.id = "main";
  document.body.prepend(el("a", { class: "skip", href: "#main" }, "본문 바로가기"));
  const hdr = $("header.top");
  if (!hdr) return;
  const right = el("div", { class: "hdr-right" });
  const sizes = [["가", 0], ["가+", 1], ["가++", 2]];
  let cur = 0;
  try { cur = +(localStorage.getItem("fs") || 0); } catch (e) {}
  const btns = sizes.map(([t, v]) => el("button", { class: "fs-btn", "aria-label": `글자 크기 ${t}`, onclick: () => setFs(v) }, t));
  function setFs(v) {
    cur = v;
    document.body.classList.remove("fs-1", "fs-2");
    if (v) document.body.classList.add("fs-" + v);
    btns.forEach((b, i) => b.classList.toggle("on", i === v));
    try { localStorage.setItem("fs", v); } catch (e) {}
  }
  setFs(cur);
  const chip = el("span", { class: "verify-chip", id: "verify-chip", title: "서비스 구조·검증 보기" }, "검증 정보 불러오는 중");
  chip.addEventListener("click", () => { const b = $("nav.tabs button[data-tab=about]"); if (b) b.click(); });
  right.append(chip, el("span", { class: "fs-group", role: "group", "aria-label": "글자 크기" }, ...btns));
  const badge = $("#llm-badge");
  if (badge) right.append(badge);
  hdr.append(right);
  api("GET", "/api/about").then(a => {
    const mc = a.model_check;
    if (mc && mc.models && mc.models.length) {
      chip.textContent = "모델 교체 실측: " + mc.models.map(m => `${m.model} ${m.passed}/${m.total}`).join(" · ");
      chip.classList.add(mc.models.every(m => m.passed === m.total) ? "ok" : "warn");
    } else chip.textContent = "모델 교체 실측 전 · 규칙 기반 동작";
    const sc = a.scorecard;
    if (sc) chip.textContent += ` · 성적표 ${sc.date}`;
  }).catch(() => { chip.textContent = "서비스 구조·검증"; });
}
document.addEventListener("DOMContentLoaded", setupA11y);
document.addEventListener("DOMContentLoaded", () => api("GET", "/api/info").then(i => {
  if (!i.public) return;
  const b = el("div", { class: "public-note", role: "note" }, "공개 시연판입니다. 모의자료로만 써 보세요(개인정보·실제 업무자료를 넣지 마세요). 넣은 자료는 서버가 다시 시작되면 지워지고, 모델은 연결하지 않은 규칙 기반 상태입니다.");
  document.body.prepend(b);
}).catch(() => {}));

// ------------------------------------------------------------ 서비스 구조·검증 화면
async function renderAbout(box) {
  box = box || $("#tab-about");
  const a = await api("GET", "/api/about");
  box.innerHTML = "";
  box.append(el("div", { class: "panel" }, el("h2", {}, "답이 만들어지는 과정"),
    el("p", { class: "small muted" }, a.lead || ""),
    el("ol", { class: "pipe" }, a.pipeline.map((p, i) => el("li", {}, el("span", { class: "pn" }, String(i + 1).padStart(2, "0")),
      el("b", {}, p[0]), el("span", { class: "small muted" }, p[1]))))));
  box.append(el("div", { class: "panel" }, el("h2", {}, `틀린 답을 막는 장치 ${a.guards.length}가지`),
    el("ol", { class: "guards" }, a.guards.map(g => el("li", {}, g)))));
  const sc = a.scorecard;
  const scBox = el("div", { class: "panel" }, el("h2", {}, "정확도 성적표"));
  if (!sc) scBox.append(el("p", { class: "muted" }, "아직 측정 결과가 없습니다. `python tools/eval.py`를 실행하면 만들어집니다."));
  else {
    scBox.append(el("p", { class: "small muted" }, `측정일 ${sc.date} · 재현: ${sc.command} (모델·인터넷 없이 같은 결과) · 문항: ${sc.author}`),
      el("table", { class: "t" }, el("tr", {}, ["시험", "결과", "무엇을 쟀나"].map(h => el("th", {}, h))),
        sc.areas.map(x => el("tr", {}, el("td", {}, el("b", {}, x.name)),
          el("td", {}, el("span", { class: "tag " + (x.passed === x.total ? "ok" : "warn") }, `${x.passed}/${x.total}` + (x.total ? ` (${Math.round(x.passed / x.total * 1000) / 10}%)` : ""))),
          el("td", { class: "small" }, x.desc)))));
    const fails = sc.areas.flatMap(x => (x.failures || []).map(f => [x.name, f]));
    scBox.append(el("details", { open: fails.length > 0 && fails.length <= 8 }, el("summary", {}, `틀린 문항 ${fails.length}건 (고치지 않고 그대로 공개)`),
      fails.length ? el("ul", { class: "small" }, fails.map(([n, f]) => el("li", {}, `[${n}] ${f}`))) : el("p", { class: "small muted" }, "없음")));
    if (sc.history && sc.history.length) scBox.append(el("details", {}, el("summary", {}, `측정 이력 ${sc.history.length}차`),
      el("ol", { class: "small" }, sc.history.map(h => el("li", { style: { marginBottom: "6px" } }, el("b", {}, h.date + (h.note ? ` · ${h.note}` : "")), el("div", { class: "muted" }, h.summary))))));
  }
  box.append(scBox);
  const mc = a.model_check;
  box.append(el("div", { class: "panel" }, el("h2", {}, "모델 교체 실측"),
    el("p", { class: "small muted" }, "같은 기능을 서로 다른 모델로 돌려 결과가 정답과 맞는지 시험합니다. 화면 머리의 표시는 이 결과입니다."),
    mc && mc.models && mc.models.length ? el("table", { class: "t" }, el("tr", {}, ["모델", "결과", "시험 항목"].map(h => el("th", {}, h))),
      mc.models.map(m => el("tr", {}, el("td", {}, m.model), el("td", {}, `${m.passed}/${m.total}`),
        el("td", { class: "small" }, m.items.map(i => (i.ok ? "통과 " : "실패 ") + i.name).join(" · ")))))
      : el("p", {}, "아직 실제 모델로 실측하지 않았습니다. 기관 PC에서 `모델검증.bat`(또는 `python tools/verify_models.py --models 모델1 모델2`)을 실행하면 여기와 화면 머리에 표시됩니다."),
    mc && mc.date ? el("p", { class: "small muted" }, `실측일 ${mc.date} · 연결 ${mc.provider}`) : null));
  if (a.data) box.append(el("div", { class: "panel" }, el("h2", {}, "자료와 개인정보"), el("ul", {}, a.data.map(d => el("li", {}, d)))));
}
