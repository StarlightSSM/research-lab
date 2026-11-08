// My Little Lab - dashboard
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const api = async (path, opts = {}) => {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};
const toast = (msg, ms = 2600) => {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms);
};

const KIND_LABEL = { paper: "논문", news: "뉴스", issue: "이슈", data: "데이터·코드" };
const state = { kind: "paper", topic: "", q: "", days: 14, overview: null, config: null };

// ---------------------------------------------------------------- theme
(() => {
  try { const t = localStorage.getItem("lab-theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("lab-theme", next); } catch {}
  };
})();

// ---------------------------------------------------------------- header & briefing
function greetingWord() {
  const h = new Date().getHours();
  return h < 6 ? "늦은 밤까지 연구 중이네요" : h < 12 ? "좋은 아침이에요" : h < 18 ? "좋은 오후예요" : "오늘도 수고했어요";
}

async function loadOverview() {
  const o = await api("/api/overview");
  state.overview = o;
  document.title = o.lab_name;
  $("#labName").textContent = o.lab_name;
  const d = new Date();
  $("#hello").textContent = `${greetingWord()}, ${o.owner}님 · ${d.toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" })}`;
  $("#schedulePill").textContent = o.schedule.enabled ? `⏰ 매일 ${o.schedule.time} 자동 수집` : "⏰ 자동 수집 꺼짐";

  // 수집 진행 표시
  $("#progressBar").classList.toggle("hidden", !o.collecting);
  $("#progressText").textContent = o.collecting ? `수집 중… ${o.progress || ""}` : "";
  $("#collectBtn").disabled = o.collecting;
  $("#collectBtn").textContent = o.collecting ? "수집 중…" : "지금 수집";

  renderBriefing(o.briefing);
  renderKpis(o.stats);
  renderTopicChips(o.topics);
  renderTopicBars(o.stats.topics);
  renderRun(o.stats.last_run);
  return o;
}

function renderBriefing(b) {
  if (!b) return;
  const c = b.content || {};
  $("#briefMeta").textContent = `${b.day} · ${b.engine}`;
  $("#briefGreeting").textContent = c.greeting || "";
  $("#briefHighlights").innerHTML = (c.highlights || []).map((h) =>
    `<li><span class="tag">${esc(h.topic)}</span><span>${esc(h.point)}</span></li>`).join("") ||
    `<li class="muted small">새 동향이 없어요</li>`;
  $("#briefMust").innerHTML = (c.must_read || []).map((m) =>
    `<li><a href="${esc(safeUrl(m.url))}" target="_blank" rel="noopener">${esc(m.title)}</a>
      <p><span class="tag">${esc(KIND_LABEL[m.kind] || m.kind)}</span> ${esc(m.reason)}</p></li>`).join("") ||
    `<li class="muted small">추천 항목 없음</li>`;
  const idea = $("#briefIdea");
  idea.classList.toggle("hidden", !c.idea);
  idea.innerHTML = c.idea ? `<b>💡 오늘의 연구 아이디어</b><br>${esc(c.idea)}` : "";
}

function renderKpis(s) {
  const tiles = [
    ["paper", "새 논문"], ["news", "새 뉴스"], ["issue", "새 이슈"], ["data", "새 데이터·코드"],
  ].map(([k, label]) => `<div class="kpi"><div class="k-label">${label}</div>
      <div class="k-value">${s.today[k] || 0}</div><div class="k-sub">최근 7일 ${s.week[k] || 0}건</div></div>`);
  tiles.push(`<div class="kpi"><div class="k-label">분석한 논문</div><div class="k-value">${s.papers_done}</div><div class="k-sub">PDF 리포트</div></div>`);
  tiles.push(`<div class="kpi"><div class="k-label">스크랩</div><div class="k-value">${s.starred}</div><div class="k-sub">읽을거리 ★</div></div>`);
  $("#kpis").innerHTML = tiles.join("");
}

function renderTopicChips(topics) {
  const all = [""].concat(topics);
  $("#topicChips").innerHTML = all.map((t) =>
    `<button class="chip ${state.topic === t ? "active" : ""}" data-topic="${esc(t)}">${t ? esc(t) : "전체"}</button>`).join("");
  $$("#topicChips .chip").forEach((b) => b.onclick = () => { state.topic = b.dataset.topic; renderTopicChips(topics); loadFeed(); });
}

function renderTopicBars(rows) {
  if (!rows || !rows.length) { $("#topicBars").innerHTML = `<p class="muted small">수집 후 표시돼요</p>`; return; }
  const max = Math.max(...rows.map((r) => r.n));
  $("#topicBars").innerHTML = rows.map((r) => `
    <div class="bar-row" data-topic="${esc(r.topic)}" title="${esc(r.topic)} · 최근 7일 ${r.n}건 (클릭하면 필터)">
      <div class="b-label"><span>${esc(r.topic)}</span><b>${r.n}</b></div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(3, (r.n / max) * 100)}%"></div></div>
    </div>`).join("");
  $$("#topicBars .bar-row").forEach((el) => el.onclick = () => {
    state.topic = el.dataset.topic; renderTopicChips(state.overview.topics); loadFeed();
    $(".feed").scrollIntoView({ behavior: "smooth" });
  });
}

function renderRun(r) {
  if (!r) return;
  const cls = { done: "status-ok", partial: "status-warn", error: "status-bad", running: "" }[r.status] || "";
  const label = { done: "성공", partial: "일부 소스 실패", error: "실패", running: "진행 중" }[r.status] || r.status;
  $("#runInfo").innerHTML = `마지막 수집 <b>${esc((r.started_at || "").replace("T", " ").slice(0, 16))}</b> ·
    <span class="${cls}">${label}</span> · 새 항목 <b>${r.new_count ?? 0}</b>건`;
  $("#runLog").textContent = r.log || "";
}

// ---------------------------------------------------------------- feed
async function loadFeed() {
  const p = new URLSearchParams({ days: state.days });
  if (state.kind === "starred") p.set("starred", "1"); else p.set("kind", state.kind);
  if (state.topic) p.set("topic", state.topic);
  if (state.q) p.set("q", state.q);
  const items = await api("/api/items?" + p);
  const list = $("#feedList");
  if (!items.length) {
    list.innerHTML = `<div class="empty">${state.kind === "starred" ? "★를 눌러 읽을거리를 모아보세요" : "항목이 없어요. 기간을 늘리거나 '지금 수집'을 눌러보세요."}</div>`;
    return;
  }
  list.innerHTML = items.map(itemHtml).join("");
  $$(".item", list).forEach((el) => {
    const id = el.dataset.id;
    $(".star", el).onclick = async (e) => {
      const on = !e.currentTarget.classList.contains("on");
      e.currentTarget.classList.toggle("on", on);
      e.currentTarget.textContent = on ? "★" : "☆";
      await api(`/api/items/${id}`, { method: "PATCH", body: JSON.stringify({ starred: on ? 1 : 0 }) });
      loadOverview();
    };
    $(".i-title", el).addEventListener("click", () => {
      el.classList.add("read");
      api(`/api/items/${id}`, { method: "PATCH", body: JSON.stringify({ is_read: 1 }) });
    });
    const sum = $(".i-sum", el);
    if (sum) sum.onclick = () => sum.classList.toggle("open");
    const an = $(".analyze", el);
    if (an) an.onclick = async () => {
      an.disabled = true; an.textContent = "PDF 받는 중…";
      try {
        await api(`/api/items/${id}/analyze`, { method: "POST" });
        toast("논문 연구실에 추가했어요. 분석이 끝나면 PDF 리포트가 생겨요.");
        an.textContent = "분석 대기열 ✓"; loadPapers();
      } catch (err) { toast(err.message, 4000); an.disabled = false; an.textContent = "AI 분석"; }
    };
  });
}

function itemHtml(it) {
  const x = it.extra || {};
  const bits = [];
  if (x.venue) bits.push(esc(x.venue));
  if (x.press) bits.push(esc(x.press));
  if (x.cited_by) bits.push(`피인용 ${x.cited_by}`);
  if (x.stars !== undefined) bits.push(`★ ${x.stars}`);
  if (x.language) bits.push(esc(x.language));
  if (x.downloads !== undefined) bits.push(`↓ ${Number(x.downloads).toLocaleString()}`);
  if (x.points !== undefined) bits.push(`▲ ${x.points} · 💬 ${x.comments}`);
  const authors = it.kind === "paper" && it.authors ? `<span>${esc(it.authors.split(", ").slice(0, 3).join(", "))}${it.authors.split(", ").length > 3 ? " 외" : ""}</span>` : "";
  return `<article class="item ${it.is_read ? "read" : ""}" data-id="${it.id}">
    <div>
      <div class="i-meta"><span class="src">${esc(it.source)}</span><span class="tag">${esc(it.topic)}</span>
        <span>${esc(it.published || "")}</span>${bits.map((b) => `<span>· ${b}</span>`).join("")}</div>
      <a class="i-title" href="${esc(safeUrl(it.url))}" target="_blank" rel="noopener">${esc(it.title)}</a>
      ${authors ? `<div class="i-meta">${authors}</div>` : ""}
      ${it.summary ? `<p class="i-sum" title="클릭하면 펼치기">${esc(it.summary)}</p>` : ""}
    </div>
    <div class="i-actions">
      <button class="star ${it.starred ? "on" : ""}" title="스크랩">${it.starred ? "★" : "☆"}</button>
      ${it.kind === "paper" && x.pdf_url ? `<button class="btn tiny analyze" title="PDF를 받아 논문 연구실에서 분석">AI 분석</button>` : ""}
      ${x.discussion ? `<a class="btn tiny" href="${esc(safeUrl(x.discussion))}" target="_blank" rel="noopener">토론</a>` : ""}
    </div>
  </article>`;
}

$$("#tabs button").forEach((b) => b.onclick = () => {
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  state.kind = b.dataset.kind; loadFeed();
});
let searchT;
$("#search").oninput = (e) => { clearTimeout(searchT); searchT = setTimeout(() => { state.q = e.target.value.trim(); loadFeed(); }, 250); };
$("#days").onchange = (e) => { state.days = +e.target.value; loadFeed(); };

// ---------------------------------------------------------------- collect
let pollT;
$("#collectBtn").onclick = async () => {
  const r = await api("/api/collect", { method: "POST" });
  toast(r.status === "busy" ? "이미 수집 중이에요" : "수집을 시작했어요. 브리핑까지 몇 분 걸릴 수 있어요.");
  pollCollect();
};
async function pollCollect() {
  clearTimeout(pollT);
  const o = await loadOverview();
  if (o.collecting) pollT = setTimeout(pollCollect, 3000);
  else loadFeed();
}

// ---------------------------------------------------------------- papers
let paperT;
async function loadPapers() {
  const ps = await api("/api/papers");
  const ul = $("#paperList");
  ul.innerHTML = ps.length ? ps.map((p) => `
    <li class="paper" data-id="${p.id}">
      <div class="p-title">${esc(p.title || p.filename)}</div>
      <div class="p-status"><span class="dot ${p.status}"></span>${esc(p.message || p.status)}${p.status === "done" && p.engine ? ` · <span class="muted">${esc(p.engine)}</span>` : ""}</div>
      <div class="p-actions">
        ${p.status === "done" ? `<button class="btn tiny view">분석 보기</button>
          <a class="btn tiny" href="/api/papers/${p.id}/pdf" target="_blank">PDF</a>
          <a class="btn tiny" href="/api/papers/${p.id}/pdf?dl=1">저장</a>` : ""}
        ${p.status === "error" || p.status === "done" ? `<button class="btn tiny retry" title="다시 분석">↻</button>` : ""}
        <button class="btn tiny del" title="삭제">삭제</button>
      </div>
    </li>`).join("") : `<li class="muted small" style="text-align:center;padding:6px">아직 분석한 논문이 없어요</li>`;
  $$(".paper", ul).forEach((el) => {
    const id = el.dataset.id;
    const v = $(".view", el); if (v) v.onclick = () => openPaper(id);
    const r = $(".retry", el); if (r) r.onclick = async () => { await api(`/api/papers/${id}/retry`, { method: "POST" }); loadPapers(); };
    $(".del", el).onclick = async () => {
      if (!confirm("이 논문과 분석 리포트를 삭제할까요?")) return;
      await api(`/api/papers/${id}`, { method: "DELETE" }); loadPapers(); loadOverview();
    };
  });
  clearTimeout(paperT);
  if (ps.some((p) => p.status === "queued" || p.status === "running")) paperT = setTimeout(loadPapers, 3000);
  else if (ps.some((p) => p.status === "done")) loadOverview();
}

async function uploadFiles(files) {
  const pdfs = [...files].filter((f) => f.name.toLowerCase().endsWith(".pdf"));
  if (!pdfs.length) return toast("PDF 파일만 올릴 수 있어요");
  const fd = new FormData();
  pdfs.forEach((f) => fd.append("file", f));
  const res = await fetch("/api/papers", { method: "POST", body: fd });
  if (!res.ok) return toast("업로드 실패");
  toast(`${pdfs.length}편 업로드 완료 · 분석을 시작해요`);
  loadPapers();
}
const drop = $("#drop");
$("#fileInput").onchange = (e) => { uploadFiles(e.target.files); e.target.value = ""; };
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

const ul = (arr) => (arr && arr.length ? `<ul>${arr.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted">논문에서 확인되지 않음</p>`);
const sec = (title, body) => `<section class="a-sec"><h3>${title}</h3>${body}</section>`;

async function openPaper(id) {
  const p = await api(`/api/papers/${id}`);
  const a = p.analysis || {};
  const m = a.meta || {};
  $("#pmTitle").textContent = m.title || p.filename;
  $("#pmMeta").textContent = [m.authors, m.venue, m.year, m.pages ? `${m.pages}쪽` : "", m.cited_by != null ? `피인용 ${m.cited_by}` : "", a.engine]
    .filter(Boolean).join(" · ");
  $("#pmOpen").href = `/api/papers/${id}/pdf`;
  $("#pmDownload").href = `/api/papers/${id}/pdf?dl=1`;
  const meth = a.methodology || {};
  const con = a.contributions || {};
  const toc = (a.toc || []).map((t) => `<li class="lv${t.level}"><span>${esc(t.title)}</span><span class="muted">${t.page}</span></li>`).join("");
  $("#pmBody").innerHTML = `
    <div class="a-tldr">${esc(a.tldr)}</div>
    <div class="kw-list">${(a.keywords || []).map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div>
    ${sec("핵심 요약", `<p>${esc(a.summary)}</p>`)}
    ${toc ? sec("목차", `<ul class="toc">${toc}</ul>`) : ""}
    ${sec("연구 질문 · 가설", ul(a.research_questions))}
    ${sec("연구방법론", `<div class="kv">
      <div>연구 설계</div><div>${esc(meth.research_design) || "-"}</div>
      <div>데이터</div><div>${esc(meth.data) || "-"}</div>
      <div>분석 기법</div><div>${esc((meth.techniques || []).join(", ")) || "-"}</div>
      <div>분석 절차</div><div>${esc(meth.analysis_procedure) || "-"}</div>
      <div>본문 탐지</div><div>${esc((a.detected_methods || []).join(", ")) || "-"}</div></div>`)}
    ${sec("주요 결과", ul(a.key_findings))}
    ${sec("연구의 기여 · 시사점", `<div class="kv"><div>이론적 기여</div><div>${esc(con.theoretical) || "-"}</div>
      <div>실무적 시사점</div><div>${esc(con.practical) || "-"}</div></div>`)}
    ${sec("쟁점 · 비판적으로 읽기", ul(a.issues))}
    ${sec("한계점", ul(a.limitations))}
    ${sec("후속 연구 아이디어", ul(a.future_research))}
    ${sec("내 연구와의 연결", `<p>${esc(a.my_research_link)}</p>`)}
    ${sec("관련 개념 · 함께 읽을 논문", `
      <div class="kw-list" style="margin:0 0 8px">${(a.related_concepts || []).map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div>
      <ul class="rel">${(a.related_papers || []).map((r) => `<li><a href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(r.title)}</a>
        <span class="muted small"> — ${esc([r.authors, r.venue, r.year, `피인용 ${r.cited_by}`].filter(Boolean).join(" · "))}</span></li>`).join("") || "<li class='muted'>찾지 못함</li>"}</ul>`)}
    ${sec("세미나 토론 질문", ul(a.discussion_questions))}
    ${(a.glossary || []).length ? sec("용어 정리", `<div class="kv">${a.glossary.map((g) => `<div>${esc(g.term)}</div><div>${esc(g.explanation)}</div>`).join("")}</div>`) : ""}
    ${a._note ? `<p class="note">※ ${esc(a._note)}</p>` : ""}`;
  $("#paperModal").showModal();
}

$$("[data-close]").forEach((b) => b.onclick = () => b.closest("dialog").close());
$$("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

// ---------------------------------------------------------------- settings
const SOURCE_LABEL = {
  arxiv: "arXiv (프리프린트)", openalex: "OpenAlex (경영·사회과학 저널 포함)", google_news_ko: "Google 뉴스 (한국어)",
  google_news_en: "Google 뉴스 (영어)", geeknews: "GeekNews (국내 IT)", hackernews: "Hacker News",
  github: "GitHub (신규 저장소)", huggingface: "HuggingFace 데이터셋",
};
const list2str = (a) => (a || []).join(", ");
const str2list = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

function topicEditor(t, i) {
  return `<div class="topic-edit" data-i="${i}">
    <div class="row"><label>주제 이름<input data-f="name" value="${esc(t.name)}"></label>
      <button class="btn tiny rm-topic" style="align-self:flex-end">삭제</button></div>
    <div class="row">
      <label>영어 키워드 (논문·해외뉴스)<input data-f="keywords_en" value="${esc(list2str(t.keywords_en))}"></label>
      <label>한국어 키워드 (국내뉴스)<input data-f="keywords_ko" value="${esc(list2str(t.keywords_ko))}"></label>
      <label>데이터셋 키워드 (짧은 영단어)<input data-f="data_keywords" value="${esc(list2str(t.data_keywords))}"></label>
    </div></div>`;
}

async function openSettings() {
  const c = await api("/api/config");
  state.config = c;
  const e = c._engines;
  $("#settingsBody").innerHTML = `
    <fieldset><legend>기본</legend><div class="row">
      <label>내 이름<input id="s-owner" value="${esc(c.owner)}"></label>
      <label>연구실 이름<input id="s-lab" value="${esc(c.lab_name)}"></label></div></fieldset>
    <fieldset><legend>매일 자동 수집</legend><div class="row">
      <label class="check"><input type="checkbox" id="s-sch" ${c.schedule.enabled ? "checked" : ""}> 사용</label>
      <label style="flex:0 0 140px">시각<input type="time" id="s-time" value="${esc(c.schedule.time)}"></label>
      <label style="flex:0 0 140px">주제·소스당 개수<input type="number" min="1" max="20" id="s-per" value="${c.limits.per_query}"></label>
      <label style="flex:0 0 140px">최근 며칠<input type="number" min="1" max="90" id="s-days" value="${c.limits.days_back}"></label></div>
      <p class="hint">서버(start_lab.bat)가 켜져 있으면 이 시각에 자동 수집해요. PC를 늦게 켜도 그날 한 번은 따라잡아요. 서버 없이도 돌리려면 setup_schedule.ps1을 실행하세요.</p></fieldset>
    <fieldset><legend>관심 연구 주제</legend><div id="topicEditors">${c.topics.map(topicEditor).join("")}</div>
      <button class="btn tiny" id="addTopic">+ 주제 추가</button>
      <p class="hint">키워드는 쉼표로 구분. 영어 키워드는 arXiv·OpenAlex·해외뉴스·HN·GitHub 검색에 쓰여요.</p></fieldset>
    <fieldset><legend>수집 소스</legend><div class="row">
      ${Object.entries(SOURCE_LABEL).map(([k, l]) => `<label class="check"><input type="checkbox" data-src="${k}" ${c.sources[k] ? "checked" : ""}> ${l}</label>`).join("")}
    </div></fieldset>
    <fieldset><legend>AI 엔진 (논문 분석 · 브리핑)</legend>
      <div class="engine-state">
        <span class="${e.ollama ? "on" : ""}">Ollama ${e.ollama ? "● 사용 가능" : "○ 없음"}</span>
        <span class="${e.gemini ? "on" : ""}">Gemini ${e.gemini ? "● 키 있음" : "○ 키 없음"}</span>
        <span class="${e.claude_cli ? "on" : ""}">Claude CLI ${e.claude_cli ? "● 설치됨" : "○ 없음"}</span>
        <span>현재 선택: <b>${esc(e.selected)}</b></span>
      </div>
      <div class="row">
        <label>엔진<select id="s-backend">
          ${[["auto", "자동 (Ollama → Gemini → Claude → 규칙)"], ["ollama", "Ollama 로컬 (무료·오프라인)"], ["gemini", "Gemini API (무료 티어, 빠름)"],
            ["claude_cli", "Claude Code CLI (내 Claude 구독)"], ["extractive", "규칙 기반 (LLM 없음)"]]
            .map(([v, l]) => `<option value="${v}" ${c.llm.backend === v ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
        <label>Ollama 모델<input id="s-omodel" value="${esc(c.llm.ollama_model)}"></label>
      </div>
      <div class="row">
        <label>Gemini API 키 <input id="s-gkey" type="password" value="${esc(c.llm.gemini_api_key)}" placeholder="aistudio.google.com/apikey 에서 무료 발급"></label>
        <label>Gemini 모델<input id="s-gmodel" value="${esc(c.llm.gemini_model)}"></label>
      </div>
      <div class="row"><button class="btn tiny" id="testEngine">엔진 테스트</button><span class="small muted" id="testOut"></span></div>
      <p class="hint">Ollama는 논문이 내 PC 밖으로 나가지 않지만 GPU가 없으면 한 편에 10분 안팎 걸려요. Gemini 무료 티어는 수십 초면 끝나지만 본문이 Google로 전송돼요.</p>
    </fieldset>`;
  $("#addTopic").onclick = () => {
    const box = $("#topicEditors");
    box.insertAdjacentHTML("beforeend", topicEditor({ name: "", keywords_en: [], keywords_ko: [], data_keywords: [] }, box.children.length));
    bindTopicRemove();
  };
  bindTopicRemove();
  $("#testEngine").onclick = async () => {
    $("#testOut").textContent = "저장 후 테스트 중… (로컬 모델은 수십 초 걸릴 수 있어요)";
    await saveSettings(false);
    try {
      const r = await api("/api/engine/test", { method: "POST" });
      $("#testOut").textContent = `${r.engine}: ${r.reply}${r.sec ? ` (${r.sec}초)` : ""}`;
    } catch (err) { $("#testOut").textContent = "실패: " + err.message; }
  };
  $("#settingsModal").showModal();
}
function bindTopicRemove() { $$(".rm-topic").forEach((b) => b.onclick = () => b.closest(".topic-edit").remove()); }

async function saveSettings(close = true) {
  const c = state.config;
  const topics = $$(".topic-edit").map((el) => {
    const g = (f) => $(`[data-f="${f}"]`, el).value;
    return { name: g("name").trim(), keywords_en: str2list(g("keywords_en")), keywords_ko: str2list(g("keywords_ko")), data_keywords: str2list(g("data_keywords")) };
  }).filter((t) => t.name);
  const sources = {};
  $$("[data-src]").forEach((cb) => sources[cb.dataset.src] = cb.checked);
  const body = {
    ...c, owner: $("#s-owner").value.trim() || "연구자", lab_name: $("#s-lab").value.trim() || "My Little Lab",
    schedule: { enabled: $("#s-sch").checked, time: $("#s-time").value || "08:00" },
    limits: { per_query: +$("#s-per").value || 5, days_back: +$("#s-days").value || 14 },
    topics, sources,
    llm: { ...c.llm, backend: $("#s-backend").value, ollama_model: $("#s-omodel").value.trim(),
      gemini_api_key: $("#s-gkey").value.trim(), gemini_model: $("#s-gmodel").value.trim() },
  };
  state.config = await api("/api/config", { method: "PUT", body: JSON.stringify(body) });
  if (close) { $("#settingsModal").close(); toast("저장했어요. 새 주제는 다음 수집부터 반영돼요."); }
  loadOverview(); updateEnginePill();
}
$("#settingsBtn").onclick = openSettings;
$("#saveSettings").onclick = () => saveSettings(true);

async function updateEnginePill() {
  const c = await api("/api/config");
  const name = { ollama: `Ollama · ${c.llm.ollama_model}`, gemini: "Gemini", claude_cli: "Claude CLI", extractive: "규칙 기반" }[c._engines.selected];
  $("#enginePill").textContent = `🧠 ${name}`;
}

// ---------------------------------------------------------------- boot
(async () => {
  try {
    const o = await loadOverview();
    loadFeed(); loadPapers(); updateEnginePill();
    if (o.collecting) pollCollect();
    setInterval(() => { if (!state.overview?.collecting) loadOverview(); }, 60000);
  } catch (e) { toast("서버에 연결하지 못했어요: " + e.message, 5000); }
})();
