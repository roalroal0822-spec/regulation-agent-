"use strict";
// 규정 제·개정 에이전트 화면

const S = { lib: null, enact: null, amend: { ops: [], preview: null }, impact: null, caseId: null, proc: null };
const OP_TYPES = { replace: "글자 바꾸기", retitle: "조 제목 바꾸기", add_item: "호 추가", add_para: "항 추가", insert: "조 신설", delete: "조 삭제", appendix_replace: "별표 글자 바꾸기", appendix_add: "별표 줄 추가" };

function phHtml(s) { return rich(s).replace(/【([^】]*)】/g, '<span class="ph">【$1】</span>'); }
function cmpTable(rows) {
  if (!rows || !rows.length) return el("p", { class: "muted" }, "바뀌는 조문이 없습니다.");
  return el("table", { class: "t cmp cmp2" }, el("tr", {}, el("th", {}, "현행"), el("th", {}, "개정안")),
    rows.map(r => el("tr", {}, el("td", { html: rich(r.old) }), el("td", { html: rich(r.new) }))));
}
function levelTag(l) { return el("span", { class: "lv-" + l }, l); }

async function loadLib() {
  S.lib = await api("GET", "/api/library");
  $("#org-label").textContent = S.lib.settings.org_name;
  const regs = S.lib.entries.filter(e => e.n_articles && e.layer === "org");
  const uppers = S.lib.entries.filter(e => e.n_articles && e.layer === "upper");
  const amSel = $("#am-reg"); const cur = amSel.value;
  amSel.innerHTML = "";
  for (const e of S.lib.entries.filter(e => e.n_articles)) amSel.append(el("option", { value: e.id, selected: e.id === cur }, `${e.short} (${e.layer === "upper" ? "상위" : "기관"})`));
  const imSel = $("#im-law"); imSel.innerHTML = "";
  for (const e of uppers) imSel.append(el("option", { value: e.id }, e.short));
  const enLaw = $("#en-law"); enLaw.innerHTML = "";
  enLaw.append(el("option", { value: "" }, "(위임 조항 없음)"));
  for (const e of uppers.concat(regs)) enLaw.append(el("option", { value: e.id }, e.short));
  if (uppers.length) { enLaw.value = uppers[0].id; }
  await fillArts();
  return S.lib;
}

// ------------------------------------------------------------ 에이전트
const AGENT_EX = [
  "체육시설 운영 규정에서 대관 사용료 납부 기한을 7일에서 5일로 줄이고, 제8조 다음에 사용허가 취소에 관한 조문을 신설해줘",
  "체육시설 정기대관 운영 지침을 새로 만들려고 해. 조례 제4조제2항 위임이고, 목적은 정기대관 신청, 대상자 선정, 사용료 납부·감면·반환과 취소 기준을 정하기 위해",
  "가람구 공공체육시설 관리 및 운영 조례가 개정됐어. 영향받는 규정 찾아줘",
  "규정집 전체 점검해줘",
];
function setupAgent() {
  const box = $("#agent-examples");
  AGENT_EX.forEach((t, i) => box.append(el("span", { class: "chip", title: t, onclick: () => { $("#agent-text").value = t; } }, ["개정 예시", "제정 예시", "상위법 개정", "전체 점검"][i])));
  $("#agent-run").addEventListener("click", runAgent);
}
async function runAgent() {
  const btn = $("#agent-run"); busy(btn, true, "처리 중");
  const out = $("#agent-out"); out.innerHTML = "";
  try {
    const r = await api("POST", "/api/agent", { text: $("#agent-text").value });
    out.append(el("div", { class: "panel" }, el("h3", {}, "처리한 순서"), el("ul", { class: "agent-steps" }, r.steps.map(s => el("li", {}, el("b", {}, s.title), " — ", s.detail)))));
    if (r.route === "amend" && r.preview) {
      out.append(amendResultPanel(r.preview, false));
      out.append(caseLinkPanel(r.case));
    } else if (r.route === "amend") {
      out.append(el("div", { class: "panel" }, el("p", {}, (r.plan.notes || []).join(" ")), el("button", { onclick: () => { goTab("amend"); $("#am-reg").value = r.entry_id; $("#am-intent").value = $("#agent-text").value; } }, "개정 화면에서 직접 작업 추가")));
    } else if (r.route === "enact") {
      S.enact = r.draft;
      out.append(el("div", { class: "panel" }, el("h3", {}, r.draft.title), el("pre", { class: "law", html: phHtml(r.draft.reg_text) }), el("p", { class: "small muted" }, r.draft.notes.join(" "))));
      out.append(caseLinkPanel(r.case));
    } else if (r.route === "impact") {
      out.append(el("div", { class: "panel" }, el("p", {}, r.message), el("button", { class: "primary", onclick: async () => { goTab("impact"); $("#im-law").value = r.law_id; await runImpact({ sample: true }); } }, "시연용 개정본으로 바로 분석")));
    } else if (r.route === "lint") {
      out.append(lintPanel(r.result));
    }
  } finally { busy(btn, false); }
}
function caseLinkPanel(c) {
  const n = c.next;
  return el("div", { class: "panel" }, el("h3", {}, "다음 할 일"),
    n ? el("div", {}, el("p", {}, n.text), el("div", { class: "row" }, n.docs.map(d => el("button", { onclick: () => download(`/api/cases/${c.id}/doc/${d.kind}?fmt=hwpx`) }, d.name + " (HWPX)")))) : null,
    el("div", { class: "row", style: { marginTop: "8px" } },
      el("button", { class: "primary", onclick: () => { S.caseId = c.id; goTab("cases"); } }, "진행 관리에서 열기"),
      el("button", { onclick: () => download(`/api/cases/${c.id}/bundle`) }, "문서 세트 전체 내려받기(ZIP)")));
}

// ------------------------------------------------------------ 제정
async function fillArts() {
  const lawId = $("#en-law").value;
  const artSel = $("#en-art"); artSel.innerHTML = ""; $("#en-para").innerHTML = "";
  if (!lawId) { $("#en-deleg-text").textContent = ""; return; }
  const d = await api("GET", `/api/library/${lawId}`);
  S.enLaw = d;
  for (const a of d.articles) artSel.append(el("option", { value: a.key }, `${a.label}(${a.title})`));
  const pick = d.articles.find(a => /세부 사항을 정할|위탁|위임/.test(a.text)) || d.articles.find(a => /필요한 사항은/.test(a.text) && !/목적/.test(a.title)) || d.articles[0];
  if (pick) artSel.value = pick.key;
  fillParas();
}
function fillParas() {
  const a = (S.enLaw && S.enLaw.articles.find(x => x.key === $("#en-art").value));
  const ps = $("#en-para"); ps.innerHTML = "";
  ps.append(el("option", { value: "" }, "조 전체"));
  if (!a) return;
  const lines = a.text.split("\n");
  const paras = lines.map(l => l.replace(/^제\d+조(의\d+)?(\([^)]*\))?\s*/, "")).filter(l => /^[①-⑳]/.test(l));
  paras.forEach((p, i) => ps.append(el("option", { value: i + 1 }, `제${i + 1}항`)));
  const pick = paras.findIndex(p => /필요한|세부|정할 수/.test(p));
  if (pick >= 0) ps.value = pick + 1;
  showDeleg();
}
function showDeleg() {
  const a = (S.enLaw && S.enLaw.articles.find(x => x.key === $("#en-art").value));
  if (!a) return;
  const p = $("#en-para").value;
  const paras = a.text.split("\n").map(l => l.replace(/^제\d+조(의\d+)?(\([^)]*\))?\s*/, "")).filter(l => /^[①-⑳]/.test(l));
  $("#en-deleg-text").textContent = p ? paras[p - 1] : a.text;
}
async function refreshMatters() {
  const r = await api("GET", `/api/enact/matters?purpose=${encodeURIComponent($("#en-purpose").value)}&title=${encodeURIComponent($("#en-title").value)}`);
  const box = $("#en-matters"); box.innerHTML = "";
  for (const m of r.matters) box.append(el("label", { class: "chip" }, el("input", { type: "checkbox", value: m.key, checked: m.on }), " " + m.title));
}
function setupEnact() {
  $("#en-law").addEventListener("change", fillArts);
  $("#en-art").addEventListener("change", fillParas);
  $("#en-para").addEventListener("change", showDeleg);
  $("#en-purpose").addEventListener("blur", refreshMatters);
  $("#en-title").addEventListener("blur", refreshMatters);
  $("#en-sample").addEventListener("click", async () => {
    $("#en-title").value = "체육시설 정기대관 운영 지침";
    $("#en-purpose").value = "체육시설을 매주 같은 요일·시간에 정기적으로 사용하려는 동호회·단체의 정기대관 신청, 대상자 선정, 사용료 납부·감면·반환과 취소 기준을 정하려고 함";
    await refreshMatters();
  });
  $("#en-run").addEventListener("click", runEnact);
  refreshMatters();
}
async function runEnact() {
  const btn = $("#en-run"); busy(btn, true, "초안 작성 중");
  try {
    const free = $("#en-deleg-free").value.trim();
    const deleg = free ? { text: free } : ($("#en-law").value ? { entry_id: $("#en-law").value, key: $("#en-art").value, para: $("#en-para").value || null } : {});
    const matters = $$("#en-matters input:checked").map(x => x.value);
    S.enact = await api("POST", "/api/enact/draft", { title: $("#en-title").value, purpose: $("#en-purpose").value, delegation: deleg, matters });
    renderEnact();
  } finally { busy(btn, false); }
}
function renderEnact() {
  const d = S.enact; const out = $("#en-out"); out.innerHTML = "";
  const arts = el("div", {});
  d.articles.forEach((a, i) => {
    const ta = el("textarea", {}, a.text);
    ta.addEventListener("input", () => { a.text = ta.value; });
    const ti = el("input", { value: a.title, style: { flex: 1 } });
    ti.addEventListener("input", () => { a.title = ti.value; });
    arts.append(el("div", { class: "art" },
      el("div", { class: "head" }, el("b", {}, `제${i + 1}조`), ti,
        a.ref ? el("span", { class: "tag info", title: a.ref.text }, `참고: ${a.ref.reg} ${a.ref.label}`) : null,
        a.upper ? el("span", { class: "tag ok" }, `상위 근거: ${a.upper.label}`) : null,
        a.by_model ? el("span", { class: "tag gray" }, "모델 작성") : null,
        el("button", { class: "small danger", onclick: () => { d.articles.splice(i, 1); rebuildEnact(); } }, "빼기")),
      a.chapter ? el("div", { class: "small muted" }, a.chapter) : null, ta));
  });
  out.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, d.title), el("span", { class: "tag gray" }, `작성: ${d.engine}`)),
    d.notes.map(n => el("p", { class: "small" }, "※ " + n)),
    el("div", { class: "grid2" },
      el("div", {}, el("h3", {}, "조문 초안 (고칠 수 있음)"), arts,
        el("div", { class: "row" }, el("button", { onclick: rebuildEnact }, "고친 내용 반영"),
          el("button", { class: "primary", onclick: async () => { const c = await api("POST", "/api/cases", { kind: "enact", draft: S.enact }); toast("진행 건으로 등록했습니다"); S.caseId = c.id; goTab("cases"); } }, "진행 건으로 등록(문서 세트 만들기)"))),
      el("div", {}, el("h3", {}, "제정안 전문"), el("pre", { class: "law", html: phHtml(d.reg_text) })))));
  out.append(el("div", { class: "panel" }, el("h3", {}, "위임 범위 검토"),
    el("table", { class: "t" }, el("tr", {}, el("th", {}, "구분"), el("th", {}, "항목"), el("th", {}, "결과")),
      d.delegation_check.items.map(i => el("tr", {}, el("td", {}, levelTag(i.level)), el("td", {}, i.item), el("td", {}, i.result))))));
  const s = d.similar;
  out.append(el("div", { class: "panel" }, el("h3", {}, "유사 규정 비교표"),
    s.regs.length ? el("table", { class: "t cmp" }, el("tr", {}, el("th", {}, "초안"), s.regs.map(r => el("th", {}, r.title))),
      s.rows.map(r => el("tr", {}, el("td", {}, el("b", {}, r.title), el("div", { class: "small", html: phHtml(r.draft) })),
        r.cells.map(c => el("td", { class: "small" }, c ? [el("b", {}, `${c.label}(${c.title})`), el("div", {}, c.text)] : el("span", { class: "muted" }, "해당 없음")))))) :
      el("p", { class: "muted" }, "비슷한 규정을 찾지 못했습니다.")));
}
async function rebuildEnact() {
  S.enact = await api("POST", "/api/enact/rebuild", { draft: S.enact });
  renderEnact();
}

// ------------------------------------------------------------ 개정
const AM_EX = [
  ["기한·신설", "대관 사용료 납부 기한을 7일에서 5일로 줄이고, 제8조 다음에 사용허가 취소에 관한 조문을 신설한다"],
  ["용어 통일", "'사용자'를 '이용자'로 통일한다"],
  ["호 추가·삭제", "제7조에 '사용료를 체납한 경우'를 호로 추가하고, 제4조를 삭제한다. 2027년 1월 1일부터 시행"],
];
function setupAmend() {
  AM_EX.forEach(([n, t]) => $("#am-examples").append(el("span", { class: "chip", title: t, onclick: () => { $("#am-intent").value = t; const sp = S.lib.entries.find(e => e.short === "체육시설 운영 규정"); if (sp) $("#am-reg").value = sp.id; } }, n)));
  $("#am-plan").addEventListener("click", planAmend);
  $("#am-view").addEventListener("click", () => viewEntry($("#am-reg").value));
}
async function planAmend() {
  const btn = $("#am-plan"); busy(btn, true, "해석 중");
  try {
    const p = await api("POST", "/api/amend/plan", { entry_id: $("#am-reg").value, intent: $("#am-intent").value });
    S.amend = { entry_id: $("#am-reg").value, ops: p.ops, notes: p.notes, engine: p.engine, effective: p.effective || "", mode: S.lib.numbering.mode };
    renderOps();
    if (p.ops.length) await previewAmend();
  } finally { busy(btn, false); }
}
function renderOps() {
  const a = S.amend; const box = $("#am-ops"); box.innerHTML = "";
  const modeSel = el("select", {}, el("option", { value: "branch", selected: a.mode === "branch" }, "가지 번호 (예: 제8조의2, 조 번호 유지)"),
    el("option", { value: "renumber", selected: a.mode === "renumber" }, "번호 이동 (뒤 조를 한 칸씩 밀고 인용 정정)"));
  modeSel.addEventListener("change", () => { a.mode = modeSel.value; previewAmend(); });
  const eff = el("input", { value: a.effective || "", placeholder: "비우면 '공포한 날부터'" });
  eff.addEventListener("change", () => { a.effective = eff.value; previewAmend(); });
  box.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h3", { style: { flex: 1, margin: 0 } }, `개정 작업 ${a.ops.length}개`), el("span", { class: "tag gray" }, `해석: ${a.engine}`)),
    (a.notes || []).map(n => el("p", { class: "small" }, "※ " + n)),
    a.ops.map((op, i) => el("div", { class: "op" }, el("span", { class: "tag info" }, OP_TYPES[op.type] || op.type), el("span", { class: "d" }, op.desc || ""),
      op.alternatives ? el("select", { onchange: e => { const alt = op.alternatives[e.target.value]; if (alt) { Object.assign(op, { article: alt.article, path: alt.path, anchor: alt.anchor, loc: alt.loc }); op.desc = `${alt.loc}: '${op.find}' → '${op.repl}'`; renderOps(); previewAmend(); } } },
        el("option", { value: "" }, "다른 위치…"), op.alternatives.map((x, k) => el("option", { value: k }, x.loc))) : null,
      el("button", { class: "small", onclick: () => editOp(i) }, "고치기"),
      el("button", { class: "small danger", onclick: () => { a.ops.splice(i, 1); renderOps(); previewAmend(); } }, "빼기"))),
    el("div", { class: "row", style: { marginTop: "8px" } },
      el("button", { onclick: () => editOp(-1) }, "작업 직접 추가"),
      el("label", { class: "small" }, "번호 방식 ", modeSel),
      el("span", { class: "small muted" }, `기본값 근거: ${S.lib.numbering.basis}`),
      el("label", { class: "small" }, "시행일 ", eff))));
}
function editOp(i) {
  const a = S.amend;
  const op = i >= 0 ? a.ops[i] : { type: "replace" };
  const type = el("select", {}, Object.entries(OP_TYPES).map(([k, v]) => el("option", { value: k, selected: op.type === k }, v)));
  const f = {
    article: el("input", { value: op.article || op.after || "", placeholder: "조 번호 (예: 8, 8의2)" }),
    find: el("input", { class: "wide", value: op.find || "", placeholder: "현행 글자" }),
    repl: el("input", { class: "wide", value: op.repl || "", placeholder: "새 글자" }),
    title: el("input", { class: "wide", value: op.title || "", placeholder: "조 제목" }),
    para: el("input", { value: op.para || "", placeholder: "항 번호(호 추가 시)" }),
    text: el("textarea", { style: { width: "100%", minHeight: "120px" } }, op.text || ""),
    after_find: el("input", { class: "wide", value: op.after_find || "", placeholder: "이 줄 다음에 추가" }),
  };
  const m = modal(i >= 0 ? "작업 고치기" : "작업 추가",
    el("label", { class: "f" }, "종류"), type,
    el("label", { class: "f" }, "조 번호 (조 신설은 '이 조 다음에')"), f.article,
    el("label", { class: "f" }, "현행 글자 / 새 글자 (글자 바꾸기)"), f.find, f.repl,
    el("label", { class: "f" }, "조 제목 (조 신설·제목 바꾸기)"), f.title,
    el("label", { class: "f" }, "항 번호 (호 추가)"), f.para,
    el("label", { class: "f" }, "내용 (조 신설·호·항·별표 줄 추가)"), f.text,
    el("label", { class: "f" }, "별표 줄 추가 위치"), f.after_find,
    el("div", { class: "row", style: { marginTop: "10px" } }, el("button", { class: "primary", onclick: () => {
      const t = type.value;
      const n = { type: t, src: "직접 입력" };
      const k = f.article.value.replace(/[제조\s]/g, "");
      if (t === "insert") Object.assign(n, { after: k, title: f.title.value, text: f.text.value });
      else if (t === "replace") Object.assign(n, { article: k || null, find: f.find.value, repl: f.repl.value, all: !k, anchor: k && op.find === f.find.value ? op.anchor : null, path: k && op.find === f.find.value ? op.path : null });
      else if (t === "retitle") Object.assign(n, { article: k, title: f.title.value });
      else if (t === "add_item") Object.assign(n, { article: k, para: f.para.value || null, text: f.text.value });
      else if (t === "add_para") Object.assign(n, { article: k, text: f.text.value });
      else if (t === "delete") Object.assign(n, { article: k });
      else if (t === "appendix_replace") Object.assign(n, { find: f.find.value, repl: f.repl.value });
      else if (t === "appendix_add") Object.assign(n, { after_find: f.after_find.value, text: f.text.value });
      if (i >= 0) a.ops[i] = n; else a.ops.push(n);
      m.remove(); renderOps(); previewAmend();
    } }, "적용")));
}
async function previewAmend() {
  const a = S.amend;
  const out = $("#am-out"); out.innerHTML = "";
  if (!a.ops.length) return;
  const pv = await api("POST", "/api/amend/preview", { entry_id: a.entry_id, ops: a.ops, mode: a.mode, effective: a.effective });
  a.preview = pv;
  pv.ops.forEach((o, i) => { if (a.ops[i]) a.ops[i].desc = o.desc; });
  $$("#am-ops .op .d").forEach((d, i) => { if (pv.ops[i]) d.textContent = pv.ops[i].desc; });
  out.append(amendResultPanel(pv, true));
}
function amendResultPanel(pv, withRegister) {
  const p = pv.propagate;
  const box = el("div", {});
  if (pv.errors.length) box.append(el("div", { class: "panel" }, el("h3", {}, "적용하지 못한 작업"), pv.errors.map(e => el("p", { class: "lv-오류" }, e))));
  box.append(el("div", { class: "panel" }, el("div", { class: "row" }, el("h3", { style: { flex: 1, margin: 0 } }, `${pv.short} 신구조문대비표`),
    el("span", { class: "small muted" }, pv.mode === "branch" ? "가지 번호" : "번호 이동")), cmpTable(pv.compare)));
  box.append(el("div", { class: "grid2" },
    el("div", { class: "panel" }, el("h3", {}, "개정문"), el("pre", { class: "law" }, pv.amendment.join("\n"))),
    el("div", { class: "panel" }, el("h3", {}, "함께 고쳐야 할 곳"),
      pv.changes.length ? [el("h4", {}, "이 규정 안 인용(자동 정정)"), pv.changes.map(c => el("div", { class: "small" }, `${c.loc}: `, c.fixes.map(x => `${x.old} → ${x.new || "확인 필요"}`).join(", ")))] : null,
      p.fixes.length ? [el("h4", {}, "다른 규정·서식의 인용(정정안)"), p.fixes.map(f => el("div", { class: "quote" }, el("b", {}, `${f.doc} ${f.loc}`), ` ${f.old_ref} → ${f.new_ref}`, el("div", { class: "small", html: rich((f.new_text || "").replace(/\n/g, " ")) })))] : null,
      p.reviews.length ? [el("h4", {}, "확인 필요"), p.reviews.map(r => el("div", { class: "quote" }, el("b", {}, `${r.doc} ${r.loc}`), ` ${r.note}`, r.suggest ? el("div", { class: "small" }, "제안: " + r.suggest) : null))] : null,
      (!pv.changes.length && !p.fixes.length && !p.reviews.length) ? el("p", { class: "muted" }, "다른 규정·별표·서식에 영향이 없습니다.") : null,
      pv.lint.length ? [el("h4", {}, "개정 후 남는 문제"), pv.lint.map(i => el("div", { class: "small" }, levelTag(i.level), ` ${i.loc}: ${i.message}`))] : null)));
  if (withRegister) box.append(el("div", { class: "panel row" },
    el("button", { class: "primary", onclick: async () => { const a = S.amend; const c = await api("POST", "/api/cases", { kind: "amend", entry_id: a.entry_id, ops: a.ops, mode: a.mode, effective: a.effective }); toast("진행 건으로 등록했습니다"); S.caseId = c.id; goTab("cases"); } }, "진행 건으로 등록(문서 세트 만들기)"),
    el("button", { onclick: () => modal("개정 후 전문", el("pre", { class: "law", style: { maxHeight: "70vh" } }, pv.new_text)) }, "개정 후 전문 보기")));
  return box;
}
async function viewEntry(id) {
  const d = await api("GET", `/api/library/${id}`);
  const body = d.articles && d.articles.length ?
    el("div", {}, d.articles.map(a => el("div", { class: "quote" }, el("pre", { class: "law", style: { border: 0, padding: 0, background: "none", maxHeight: "none" } }, a.text))),
      d.appendices && d.appendices.length ? el("pre", { class: "law" }, d.appendices.join("\n")) : null) :
    el("pre", { class: "law" }, (d.lines || []).join("\n"));
  modal(d.title, el("p", { class: "small muted" }, `${d.rel} · ${d.kind}`), body,
    el("button", { onclick: () => location.href = `/api/library/${id}/file` }, "파일 내려받기(사본)"));
}

// ------------------------------------------------------------ 상위법 영향
function setupImpact() {
  $("#im-sample").addEventListener("click", () => runImpact({ sample: true }));
  $("#im-run").addEventListener("click", async () => {
    const fs = $("#im-file").files; if (!fs.length) { toast("개정 후 법령 파일을 고르세요", true); return; }
    const [f] = await readFiles(fs); await runImpact({ file: f });
  });
  $("#im-run-text").addEventListener("click", () => runImpact({ text: $("#im-text").value }));
}
async function runImpact(extra) {
  const out = $("#im-out"); out.innerHTML = "";
  const r = await api("POST", "/api/impact", Object.assign({ law_id: $("#im-law").value }, extra));
  S.impact = r;
  out.append(el("div", { class: "panel" }, el("h3", {}, `${r.law} 개정 전후 비교 (${r.new_name})`),
    el("table", { class: "t cmp" }, el("tr", {}, el("th", { style: { width: "110px" } }, "구분"), el("th", {}, "개정 전"), el("th", {}, "개정 후")),
      r.changes.map(c => el("tr", {}, el("td", {}, el("span", { class: "tag " + (c.kind === "번호 이동" ? "gray" : "warn") }, c.kind)), el("td", { html: rich(c.old), style: { width: "45%" } }), el("td", { html: rich(c.new), style: { width: "45%" } }))))));
  const checks = {};
  const rows = r.candidates.map(c => {
    const cb = el("input", { type: "checkbox", checked: !!c.op, disabled: !c.op });
    checks[c.id] = cb;
    return el("tr", {}, el("td", {}, cb), el("td", {}, el("b", {}, c.doc), el("div", { class: "small muted" }, c.loc)),
      el("td", {}, el("span", { class: "tag " + (c.op ? "info" : "warn") }, c.type)), el("td", { class: "small" }, c.reason,
        c.suggest ? el("div", { class: "quote", html: "제안: " + rich(c.suggest) }) : null));
  });
  out.append(el("div", { class: "panel" }, el("h3", {}, `영향받는 기관 규정·서식 ${r.candidates.length}곳`),
    r.candidates.length ? el("table", { class: "t" }, el("tr", {}, el("th", {}, "반영"), el("th", {}, "문서·위치"), el("th", {}, "종류"), el("th", {}, "내용")), rows) : el("p", { class: "muted" }, "영향받는 곳을 찾지 못했습니다."),
    el("div", { class: "row", style: { marginTop: "10px" } },
      el("button", { class: "primary", onclick: async () => {
        const ids = Object.entries(checks).filter(([k, v]) => v.checked).map(([k]) => k);
        const res = await api("POST", "/api/impact/cases", { ids });
        toast(`개정 건 ${res.made.length}개를 만들었습니다`);
        if (res.made.length) { S.caseId = res.made[0].id; goTab("cases"); }
      } }, "선택한 후보로 개정 건 만들기"),
      el("button", { onclick: async () => { if (!confirm("규정집의 상위 법령을 새 판으로 바꿀까요? (이전 판은 보관됩니다)")) return; await api("POST", "/api/impact/replace_law"); toast("새 판으로 바꿨습니다"); loadLib(); } }, "규정집의 상위 법령을 새 판으로 교체"))));
}

// ------------------------------------------------------------ 전체 점검
function lintPanel(r) {
  return el("div", { class: "panel" },
    el("div", { class: "row" }, el("h3", { style: { flex: 1, margin: 0 } }, `문서 ${r.n_docs}건 · 규정 ${r.n_regs}건 · 조문 ${r.n_articles}개 점검`),
      Object.entries(r.summary).map(([k, v]) => el("span", { class: "stat" }, el("b", { class: "lv-" + k }, v), " " + k))),
    r.issues.length ? el("table", { class: "t" }, el("tr", {}, el("th", {}, "수준"), el("th", {}, "종류"), el("th", {}, "문서"), el("th", {}, "위치"), el("th", {}, "내용"), el("th", {}, "")),
      r.issues.map(i => el("tr", {}, el("td", {}, levelTag(i.level)), el("td", {}, i.kind), el("td", {}, i.doc), el("td", {}, i.loc), el("td", { class: "small" }, i.message),
        el("td", {}, i.fix ? el("button", { class: "small", onclick: async () => { const c = await api("POST", "/api/lint/fix", { entry_id: i.entry_id, fix: i.fix, loc: i.loc }); toast("정정 개정 건을 만들었습니다"); S.caseId = c.id; goTab("cases"); } }, "정정 건 만들기") : null)))) :
      el("p", {}, "발견된 문제가 없습니다."));
}
function setupLint() {
  $("#lint-export").append(exportButtons("/api/lint/export"));
  $("#lint-run").addEventListener("click", async () => {
    const btn = $("#lint-run"); busy(btn, true, "점검 중");
    try { const r = await api("GET", "/api/lint"); $("#lint-out").innerHTML = ""; $("#lint-out").append(lintPanel(r)); } finally { busy(btn, false); }
  });
}

// ------------------------------------------------------------ 진행 관리
async function loadCases() {
  const r = await api("GET", "/api/cases");
  const box = $("#case-list"); box.innerHTML = "";
  if (!r.cases.length) box.append(el("p", { class: "muted small" }, "등록된 건이 없습니다."));
  for (const c of r.cases) box.append(el("div", { class: "c" + (c.id === S.caseId ? " on" : ""), onclick: () => { S.caseId = c.id; loadCases(); } },
    el("div", {}, el("span", { class: "tag " + (c.kind === "enact" ? "ok" : "info") }, c.kind === "enact" ? "제정" : "개정"), " ", el("b", {}, c.title.replace(S.lib.settings.org_name, "").trim())),
    el("div", { class: "small muted" }, c.created + (c.published ? " · 규정집 반영됨" : c.done ? " · 절차 완료" : "")),
    c.next ? el("div", { class: "small" }, "다음: " + c.next) : null));
  if (S.caseId && r.cases.some(c => c.id === S.caseId)) showCase(S.caseId);
}
function docButtons(cid, d) {
  return el("span", { class: "row", style: { gap: "4px" } }, el("b", { class: "small" }, d.name),
    el("button", { class: "small", onclick: () => previewDoc(cid, d) }, "보기"),
    el("button", { class: "small", onclick: () => download(`/api/cases/${cid}/doc/${d.kind}?fmt=hwpx`) }, "HWPX"),
    el("button", { class: "small", onclick: () => download(`/api/cases/${cid}/doc/${d.kind}?fmt=docx`) }, "DOCX"));
}
async function previewDoc(cid, d) {
  const r = await api("GET", `/api/cases/${cid}/preview/${d.kind}`);
  const box = el("div", { class: "doc-preview" });
  for (const b of r.blocks) {
    const [k, v] = b;
    if (k === "title") box.append(el("h2", { style: { textAlign: "center" } }, v));
    else if (k === "h1" || k === "h2" || k === "h3") box.append(el("h3", {}, v));
    else if (k === "p" || k === "note") box.append(el("p", { html: rich(v) }));
    else if (k === "bullet") box.append(el("p", { html: "• " + rich(v) }));
    else if (k === "table") box.append(el("table", { class: "t cmp" }, el("tr", {}, b[1].map(h => el("th", {}, h))), b[2].map(row => el("tr", {}, row.map(c => el("td", { html: rich(String(c)) }))))));
  }
  modal(d.name, box);
}
async function showCase(cid) {
  const c = await api("GET", `/api/cases/${cid}`);
  const box = $("#case-detail"); box.innerHTML = "";
  const stepEls = c.steps.map(s => {
    const date = el("input", { type: "date", value: (s.started || S.lib.today), style: { width: "140px" } });
    const note = el("input", { value: s.note || "", placeholder: "메모(협의 결과 등)", style: { flex: 1 } });
    const act = async status => { await api("POST", `/api/cases/${cid}/step`, { step_id: s.id, status, date: date.value, note: note.value }); showCase(cid); loadCasesQuiet(); };
    return el("li", { class: s.status },
      el("div", {}, el("b", {}, s.title), " ", el("span", { class: "small muted" }, `${s.basis.reg} ${s.basis.label}`),
        s.status === "done" ? el("span", { class: "tag ok" }, `완료 ${s.done_at}`) : s.status === "skipped" ? el("span", { class: "tag gray" }, "생략") : s.status === "doing" ? el("span", { class: "tag info" }, "진행 중") : null,
        s.due && s.status === "doing" ? el("span", { class: "tag warn" }, `기한 ${s.due}`) : null),
      el("div", { class: "small" }, s.action),
      s.days ? el("div", { class: "small muted" }, `기간: ${s.days.n}일 ${s.days.kind}`) : null,
      s.condition ? el("div", { class: "small muted" }, `조건: ${s.condition}`) : null,
      s.optional ? el("div", { class: "small muted" }, `예외: ${s.optional}`) : null,
      s.docs.length ? el("div", { class: "row", style: { flexWrap: "wrap", gap: "10px", marginTop: "4px" } }, s.docs.map(k => docButtons(cid, { kind: k, name: S.proc.doc_names[k] }))) : null,
      el("div", { class: "row", style: { marginTop: "4px" } }, date, note,
        s.status !== "done" ? el("button", { class: "small primary", onclick: () => act("done") }, "완료") : el("button", { class: "small", onclick: () => act("doing") }, "다시 진행"),
        s.status === "todo" ? el("button", { class: "small", onclick: () => act("doing") }, "시작") : null,
        (s.optional || s.condition) && s.status !== "skipped" ? el("button", { class: "small", onclick: () => act("skipped") }, "생략") : null));
  });
  box.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, c.title + (c.kind === "enact" ? " 제정" : " 개정")),
      el("button", { class: "small danger", onclick: async () => { if (!confirm("이 건을 지울까요?")) return; await api("DELETE", `/api/cases/${cid}`); S.caseId = null; box.innerHTML = ""; loadCases(); } }, "건 삭제")),
    el("p", { class: "small muted" }, `등록 ${c.created} · 절차 근거: ${c.proc_source || "일반 절차"}` + (c.source === "impact" ? ` · 「${c.law}」 개정 반영` : "")),
    c.next ? el("div", { class: "quote hit" }, el("b", {}, "다음 할 일: "), c.next.text) : el("div", { class: "quote" }, "모든 절차를 마쳤습니다."),
    el("h3", {}, "절차"), el("ol", { class: "steps2" }, stepEls)));
  box.append(el("div", { class: "panel" }, el("h3", {}, "문서 세트"),
    el("div", { style: { display: "grid", gap: "6px" } }, c.available_docs.map(d => docButtons(cid, d))),
    el("div", { class: "row", style: { marginTop: "10px" } },
      el("button", { class: "primary", onclick: () => download(`/api/cases/${cid}/bundle`) }, "전체 세트 내려받기(ZIP, 서식 정정본 포함)"),
      c.kind === "amend" && (c.propagate.fixes || []).some(f => f.layer === "org") ? el("button", { onclick: async () => { const r = await api("POST", `/api/cases/${cid}/related`); toast(`관련 규정 개정 건 ${r.made.length}개를 만들었습니다`); loadCases(); } }, "인용 정정이 필요한 다른 규정도 개정 건 만들기") : null,
      el("button", { onclick: async () => { if (!confirm("공포된 것으로 보고 규정집(작업 폴더 사본)에 반영할까요? 이전 판은 보관됩니다.")) return; const r = await api("POST", `/api/cases/${cid}/publish`); toast(`규정집에 반영했습니다: ${r.file}`); await loadLib(); showCase(cid); } }, c.published ? `규정집 반영됨(${c.published}) - 다시 반영` : "규정집에 반영(공포 후)"))));
  if (c.kind === "amend") box.append(el("div", { class: "panel" }, el("h3", {}, "신구조문대비표"), cmpTable(c.compare)));
  else box.append(el("div", { class: "panel" }, el("h3", {}, "제정안 전문"), el("pre", { class: "law", html: phHtml(c.new_text) })));
  box.append(el("div", { class: "panel" }, el("h3", {}, "처리 이력"), c.history.slice().reverse().map(h => el("div", { class: "small" }, `${h.at} ${h.what} ${h.detail || ""}`))));
}
async function loadCasesQuiet() {
  const r = await api("GET", "/api/cases");
  $$("#case-list .c").forEach((n, i) => { const c = r.cases[i]; if (c) n.classList.toggle("on", c.id === S.caseId); });
}

// ------------------------------------------------------------ 규정집
function renderLibrary() {
  const cols = $("#lib-cols"); cols.innerHTML = "";
  for (const [layer, name] of Object.entries(S.lib.layers)) {
    const fileIn = el("input", { type: "file", multiple: true });
    const list = S.lib.entries.filter(e => e.layer === layer);
    const pasteName = el("input", { class: "wide", placeholder: "법령·조례 이름" });
    const pasteText = el("textarea", { style: { width: "100%", minHeight: "90px" }, placeholder: "국가법령정보센터 등에서 복사한 본문" });
    cols.append(el("div", { class: "panel" }, el("h3", {}, name, el("span", { class: "small muted" }, ` ${list.length}건`)),
      list.map(e => el("div", { class: "row", style: { justifyContent: "space-between", borderBottom: "1px solid var(--line)", padding: "4px 0" } },
        el("a", { href: "#", onclick: ev => { ev.preventDefault(); viewEntry(e.id); } }, e.short),
        el("span", { class: "small muted" }, e.n_articles ? `${e.kind} · ${e.n_articles}조` : (e.form_of ? `${e.form_of} 별지${e.form_no}` : e.ext)),
        el("button", { class: "small danger", onclick: async () => { if (!confirm(`${e.short}을(를) 규정집에서 뺄까요? (작업 폴더 사본만 빠지고 보관됩니다)`)) return; await api("DELETE", `/api/library/${e.id}`); await loadLib(); renderLibrary(); } }, "빼기"))),
      el("div", { class: "row", style: { marginTop: "8px" } }, fileIn, el("button", { onclick: async () => {
        if (!fileIn.files.length) return;
        const files = await readFiles(fileIn.files);
        const r = await api("POST", `/api/library/${layer}/upload`, { files });
        toast(`등록 ${r.saved.length}건` + (r.skipped.length ? `, 제외 ${r.skipped.length}건` : "")); await loadLib(); renderLibrary();
      } }, "등록")),
      layer !== "forms" ? el("details", {}, el("summary", { class: "small" }, "본문 붙여넣어 등록"), pasteName, pasteText,
        el("button", { onclick: async () => { await api("POST", `/api/library/${layer}/text`, { name: pasteName.value, text: pasteText.value }); toast("등록했습니다"); await loadLib(); renderLibrary(); } }, "등록")) : null));
  }
}

// ------------------------------------------------------------ 설정·기록
const SET_FIELDS = [["org_name", "기관 이름"], ["head", "기관장 호칭(조문 주어)"], ["dept", "규정 주관부서"], ["phone", "의견 제출 전화"], ["email", "의견 제출 전자우편"]];
async function renderSettings() {
  const s = await api("GET", "/api/settings");
  const f = $("#set-form"); f.innerHTML = "";
  for (const [k, n] of SET_FIELDS) f.append(el("label", { class: "f" }, n), el("input", { class: "wide", id: "set-" + k, value: s[k] || "" }));
  f.append(el("label", { class: "f" }, "용어 묶음 (한 줄에 하나, 첫 낱말이 표준. 예: 이용자, 사용자)"),
    el("textarea", { id: "set-terms", style: { width: "100%", minHeight: "90px" } }, (s.terms || []).map(g => g.join(", ")).join("\n")));
  S.proc = await api("GET", "/api/procedure");
  const pv = $("#proc-view"); pv.innerHTML = "";
  pv.append(el("p", { class: "small muted" }, `근거: ${S.proc.source || "규정관리규정을 찾지 못해 일반 절차 사용"} · 조 신설 번호 방식: ${S.proc.numbering.mode === "branch" ? "가지 번호" : "번호 이동"} (${S.proc.numbering.basis})`),
    el("ol", { class: "steps2" }, S.proc.steps.map(s => el("li", {}, el("b", {}, s.title), " ", el("span", { class: "small muted" }, `${s.basis.reg} ${s.basis.label}`),
      el("div", { class: "small" }, s.action), s.docs.length ? el("div", { class: "small muted" }, "준비 문서: " + s.docs.map(d => S.proc.doc_names[d]).join(", ")) : null))));
  const h = await api("GET", "/api/history");
  const hb = $("#hist"); hb.innerHTML = "";
  hb.append(h.history.length ? el("table", { class: "t" }, el("tr", {}, el("th", {}, "시각"), el("th", {}, "작업"), el("th", {}, "내용")),
    h.history.map(x => el("tr", {}, el("td", { class: "small" }, x.at), el("td", {}, x.action), el("td", { class: "small" }, x.detail)))) : el("p", { class: "muted" }, "기록이 없습니다."));
}
function setupSettings() {
  $("#set-save").addEventListener("click", async () => {
    const b = {};
    for (const [k] of SET_FIELDS) b[k] = $("#set-" + k).value;
    b.terms = $("#set-terms").value.split("\n").map(l => l.split(/[,，]/).map(x => x.trim()).filter(Boolean)).filter(g => g.length > 1);
    await api("POST", "/api/settings", b); toast("저장했습니다"); await loadLib(); renderSettings();
  });
  $("#lib-sample").addEventListener("click", async () => { await api("POST", "/api/library/sample"); toast("모의 규정집을 다시 불러왔습니다"); await loadLib(); renderLibrary(); });
}

let goTab = null;
document.addEventListener("DOMContentLoaded", async () => {
  await loadLib();
  S.proc = await api("GET", "/api/procedure");
  setupAgent(); setupEnact(); setupAmend(); setupImpact(); setupLint(); setupSettings();
  goTab = setupTabs(id => {
    if (id === "cases") loadCases();
    if (id === "library") renderLibrary();
    if (id === "settings") renderSettings();
  });
});
