"""업로드한 논문 PDF 분석: 텍스트/목차 추출 -> (LLM 또는 규칙 기반) 구조화 요약 -> 관련 논문 탐색."""
import re
import urllib.parse
from collections import Counter
from datetime import datetime

import pymupdf as fitz  # PyMuPDF

from . import llm
from .collectors import get_json

# ------------------------------------------------------------------ 1. PDF 파싱
HEADING_ROLES = [
    ("abstract", r"^(abstract|요\s*약|국문\s*초록|초록)$"),
    ("intro", r"introduction|서\s*론|들어가며|문제\s*제기"),
    ("background", r"literature|background|related work|theor|prior research|hypothes|이론적|선행\s*연구|가설|문헌"),
    ("method", r"method|research design|data|sample|measure|empirical|model|experiment|연구\s*방법|연구\s*설계|자료|데이터|측정|분석\s*방법|표본"),
    ("results", r"result|finding|analysis|estimation|evaluation|분석\s*결과|실증|결과"),
    ("discussion", r"discussion|implication|논의|시사점|토의"),
    ("limitations", r"limitation|future research|future work|한계|향후\s*연구"),
    ("conclusion", r"conclu|summary|맺음말|결\s*론"),
    ("references", r"^(references|bibliography|참고\s*문헌|works cited)$"),
]
NUMBERED = re.compile(r"^((\d+(\.\d+){0,3})\.?|[IVX]{1,5}\.|[A-H]\.|제\s*\d+\s*장|[ⅠⅡⅢⅣⅤⅥⅦ]\.?)\s+\S")
KNOWN = re.compile(r"^(abstract|introduction|literature review|background|related work|methodology|methods?|"
                   r"data( and methods?)?|results?|discussion|conclusions?|limitations?|references|"
                   r"서론|결론|참고문헌|연구방법|연구 방법|분석 결과|이론적 배경|선행연구|요약|초록)\b", re.I)


NUM_PREFIX = re.compile(r"^((\d+(\.\d+){0,3})\.?|[IVX]{1,5}\.|[A-H]\.|제\s*\d+\s*장|[ⅠⅡⅢⅣⅤⅥⅦ]\.?)\s+")


def _role(title):
    t = NUM_PREFIX.sub("", title.strip()).lower()
    for role, pat in HEADING_ROLES:
        if re.search(pat, t, re.I):
            return role
    return "other"


def parse_pdf(path):
    doc = fitz.open(path)
    lines = []  # (page, text, size, bold)
    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict").get("blocks", []):
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = " ".join(s["text"].strip() for s in spans).strip()
                size = round(max(s["size"] for s in spans), 1)
                bold = all((s["flags"] & 16) or "bold" in s["font"].lower() for s in spans)
                lines.append((pno, text, size, bold))
    meta = doc.metadata or {}
    page_count = doc.page_count
    toc_native = doc.get_toc()
    doc.close()

    if not lines:
        raise ValueError("PDF에서 텍스트를 추출하지 못했습니다 (스캔 이미지 PDF일 수 있어요)")

    # 본문 글자 크기 = 가장 많은 글자가 쓰인 크기
    size_weight = Counter()
    for _, t, s, _ in lines:
        size_weight[s] += len(t)
    body = size_weight.most_common(1)[0][0]

    # 매 페이지 반복되는 머리말/꼬리말 제거
    freq = Counter(t for _, t, _, _ in lines)
    repeated = {t for t, n in freq.items() if n >= 3 and len(t) < 120}

    # 제목: 메타데이터 우선, 아니면 1페이지의 가장 큰 글씨
    title = (meta.get("title") or "").strip()
    if len(title) < 8 or re.search(r"microsoft word|untitled|\.docx?|\.pdf", title, re.I):
        p1 = [(t, s) for p, t, s, _ in lines if p == 1 and t not in repeated and len(t) > 2]
        if p1:
            mx = max(s for _, s in p1)
            title = " ".join(t for t, s in p1 if s == mx)[:250]

    # 목차/섹션 탐지
    headings = []
    for i, (p, t, s, b) in enumerate(lines):
        if t in repeated or not (3 <= len(t) <= 100) or not re.search(r"[A-Za-z가-힣]", t):
            continue
        if p == 1 and t in title:
            continue
        if t.endswith((".", ",", ";")) and not NUMBERED.match(t):
            continue
        bigger = s >= body + 1.2
        is_known = bool(KNOWN.match(t)) and len(t) < 40
        numbered = bool(NUMBERED.match(t)) and len(t.split()) <= 12 and (b or bigger)
        if (bigger and len(t.split()) <= 14) or (b and is_known) or numbered or (is_known and s >= body):
            if re.match(r"^(table|fig(ure)?|그림|표)\s*\d", t, re.I):
                continue
            m = re.match(r"^(\d+(\.\d+)*)", t)
            level = m.group(1).count(".") + 1 if m else 1
            headings.append({"idx": i, "title": t, "page": p, "level": min(level, 3)})

    # 너무 많이 잡히면(본문이 큰 글씨인 경우 등) 알려진 제목/번호 제목만
    if len(headings) > 45:
        headings = [h for h in headings if KNOWN.match(h["title"]) or NUMBERED.match(h["title"])]

    if toc_native and len(toc_native) >= 3:
        toc = [{"level": min(l, 3), "title": t.strip(), "page": pg} for l, t, pg in toc_native][:60]
    else:
        toc = [{k: h[k] for k in ("level", "title", "page")} for h in headings][:60]

    # 섹션 본문 분리
    sections = []
    bounds = [h["idx"] for h in headings] + [len(lines)]
    if headings:
        pre = " ".join(t for _, t, _, _ in lines[: bounds[0]] if t not in repeated)
        if pre:
            sections.append({"title": "(front)", "role": "front", "text": pre})
    for h, end in zip(headings, bounds[1:]):
        body_text = " ".join(t for _, t, _, _ in lines[h["idx"] + 1: end] if t not in repeated)
        body_text = re.sub(r"(?<=[a-z])-\s+(?=[a-z])", "", body_text)  # 줄바꿈 하이픈 복원
        sections.append({"title": h["title"], "role": _role(h["title"]), "text": body_text})

    # 참고문헌 이후는 본문 분석에서 제외
    ref_idx = [h["idx"] for h in headings if _role(h["title"]) == "references"]
    cut = ref_idx[-1] if ref_idx and ref_idx[-1] > len(lines) * 0.4 else len(lines)
    full = " ".join(t for _, t, _, _ in lines[:cut] if t not in repeated)
    full = re.sub(r"(?<=[a-z])-\s+(?=[a-z])", "", full)  # 줄바꿈 하이픈 복원

    # 초록 / 저자 키워드
    abstract = next((s["text"] for s in sections if s["role"] == "abstract"), "")
    if not abstract:
        m = re.search(r"(abstract|요\s*약|초\s*록)[\s:.\-—]*(.{200,2500}?)(keywords|key words|index terms|주제어|1\.?\s*introduction|introduction|서\s*론)",
                      full[:15000], re.I | re.S)
        if m:
            abstract = m.group(2)
    kw = []
    m = re.search(r"(keywords|key words|index terms|주제어|핵심어|키워드)\s*[:：—\-]?\s*(.{5,300}?)(?=(\s1\.?\s|introduction|서\s*론|jel|abstract|$))",
                  full[:20000], re.I | re.S)
    if m:
        kw = [k.strip(" .") for k in re.split(r"[,;·•]|\s{2,}", m.group(2)) if 2 < len(k.strip()) < 50][:10]

    return {
        "title": title, "authors": (meta.get("author") or "").strip(), "pages": page_count,
        "toc": toc, "sections": sections, "full": full, "abstract": abstract.strip()[:3000],
        "author_keywords": kw,
    }


# ------------------------------------------------------------------ 2. LLM 입력 선택
def select_text(doc, budget):
    secs = [s for s in doc["sections"] if s["role"] != "references"]
    full = doc["full"]
    ref = re.search(r"\b(references|bibliography|참고문헌)\b", full[len(full) // 2:], re.I)
    if ref:
        full = full[: len(full) // 2 + ref.start()]
    if len(full) <= budget:
        return full
    if len(secs) < 3:  # 섹션 탐지 실패 -> 앞 60% + 뒤 40%
        return full[: int(budget * 0.6)] + "\n...\n" + full[-int(budget * 0.4):]
    quota = {"front": 0.12, "abstract": 0.08, "intro": 0.14, "background": 0.08, "method": 0.22,
             "results": 0.14, "discussion": 0.1, "limitations": 0.08, "conclusion": 0.08, "other": 0.04}
    by_role = {}
    for s in secs:
        by_role.setdefault(s["role"], []).append(s)
    parts = []
    for s in secs:
        n = len(by_role[s["role"]])
        cap = int(budget * quota.get(s["role"], 0.04) / n)
        if cap > 200:
            parts.append(f"\n## {s['title']}\n{s['text'][:cap]}")
    return "".join(parts)[:budget]


# ------------------------------------------------------------------ 3. LLM 분석
FIELDS_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"}, "authors": {"type": "string"}, "year": {"type": "string"},
        "venue": {"type": "string"},
        "tldr": {"type": "string"},
        "summary": {"type": "string"},
        "research_questions": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "methodology": {"type": "object", "properties": {
            "research_design": {"type": "string"}, "data": {"type": "string"},
            "techniques": {"type": "array", "items": {"type": "string"}},
            "analysis_procedure": {"type": "string"}},
            "required": ["research_design", "data", "techniques", "analysis_procedure"]},
        "key_findings": {"type": "array", "items": {"type": "string"}},
        "contributions": {"type": "object", "properties": {
            "theoretical": {"type": "string"}, "practical": {"type": "string"}},
            "required": ["theoretical", "practical"]},
        "issues": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "future_research": {"type": "array", "items": {"type": "string"}},
        "related_concepts": {"type": "array", "items": {"type": "string"}},
        "my_research_link": {"type": "string"},
        "discussion_questions": {"type": "array", "items": {"type": "string"}},
        "glossary": {"type": "array", "items": {"type": "object", "properties": {
            "term": {"type": "string"}, "explanation": {"type": "string"}}, "required": ["term", "explanation"]}},
    },
    "required": ["title", "tldr", "summary", "research_questions", "keywords", "methodology", "key_findings",
                 "contributions", "issues", "limitations", "future_research", "related_concepts",
                 "my_research_link", "discussion_questions", "glossary"],
}

SYSTEM = (
    "당신은 경영학·빅데이터 분야 석사과정생의 연구 조교입니다. 학술 논문을 정확하게 읽고, "
    "한국어로 명료하게 정리합니다. 논문에 없는 내용을 지어내지 말고, 논문에서 확인되지 않으면 '논문에 명시되지 않음'이라고 쓰세요. "
    "전문 용어는 한국어(영어 원어) 형식으로 병기하세요."
)


def build_prompt(doc, text, interests):
    return f"""다음 논문을 분석해 JSON으로 정리하세요. 모든 값은 한국어로 작성합니다(논문 제목·저자·학술지명은 원문 유지).

[나의 관심 연구 주제] {interests}

[작성 규칙]
- title, authors, year, venue: 논문의 서지 정보
- tldr: 한 문장 핵심 요약 (60자 내외)
- summary: 연구 배경-목적-방법-결과-의의 흐름의 5~7문장 요약
- research_questions: 연구 질문 또는 가설 (2~5개)
- keywords: 핵심 키워드 6~10개
- methodology: research_design(연구 설계 유형), data(데이터 출처·규모·기간·표본), techniques(사용된 분석 기법 목록), analysis_procedure(분석 절차 2~4문장)
- key_findings: 주요 결과 3~6개 (가능하면 수치 포함)
- contributions: theoretical(이론적 기여), practical(실무적·경영적 시사점)
- issues: 이 논문을 둘러싼 쟁점·논쟁점·비판적으로 볼 지점 3~5개 (가정의 타당성, 인과 식별, 측정, 일반화 가능성 등)
- limitations: 한계점 3~5개 (저자가 밝힌 것 + 비판적으로 보이는 것)
- future_research: 후속 연구 아이디어 3~5개
- related_concepts: 함께 공부하면 좋은 관련 이론·개념·방법론 4~8개
- my_research_link: 이 논문을 나의 관심 연구 주제와 연결할 수 있는 아이디어 2~4문장
- discussion_questions: 랩 세미나/수업에서 던질 만한 토론 질문 3개
- glossary: 어려운 용어 3~6개와 쉬운 설명

[논문 제목(추출)] {doc['title']}
[저자 키워드(추출)] {', '.join(doc['author_keywords']) or '없음'}

[논문 본문]
{text}
"""


# ------------------------------------------------------------------ 4. 규칙 기반 분석 (LLM 없을 때)
METHOD_DICT = {
    "회귀분석(Regression)": r"\b(ols|linear regression|regression analys[ie]s|regression model)|회귀\s*분석",
    "로지스틱 회귀(Logistic Regression)": r"logistic regression|logit model|로지스틱",
    "패널 데이터 분석(Panel Data)": r"panel data|fixed[- ]effects?|random[- ]effects?|패널",
    "이중차분법(DID)": r"difference[- ]in[- ]differences?|\bdid\b|이중\s*차분",
    "성향점수매칭(PSM)": r"propensity score|성향\s*점수",
    "도구변수(IV)": r"instrumental variables?|\b2sls\b|도구\s*변수",
    "회귀불연속(RDD)": r"regression discontinuity",
    "구조방정식(SEM)": r"structural equation|\bsem\b|구조\s*방정식",
    "PLS-SEM": r"pls[- ]sem|partial least squares",
    "요인분석(Factor Analysis)": r"factor analysis|\bcfa\b|\befa\b|요인\s*분석",
    "매개·조절효과 분석": r"mediat|moderat|매개\s*효과|조절\s*효과",
    "분산분석(ANOVA)": r"\banova\b|analysis of variance|분산\s*분석",
    "다층모형(HLM)": r"hierarchical linear|multilevel|다층",
    "설문조사(Survey)": r"\bsurvey|questionnaire|respondents|설문",
    "인터뷰/질적연구": r"interview|qualitative|grounded theory|인터뷰|면담|질적",
    "사례연구(Case Study)": r"case stud(y|ies)|사례\s*연구",
    "실험/A·B 테스트": r"\bexperiment|a/b test|randomi[sz]ed|field experiment|실험",
    "컨조인트 분석": r"conjoint",
    "사건연구(Event Study)": r"event study|abnormal return",
    "시계열 분석": r"time[- ]series|arima|\bvar model|granger|시계열",
    "토픽모델링(LDA/BERTopic)": r"\blda\b|latent dirichlet|topic model|bertopic|토픽\s*모델",
    "감성분석(Sentiment Analysis)": r"sentiment|감성\s*분석|감정\s*분석",
    "텍스트마이닝": r"text mining|natural language processing|\bnlp\b|텍스트\s*마이닝",
    "단어 임베딩(Word2Vec 등)": r"word2vec|word embedding|glove|doc2vec",
    "BERT/트랜스포머": r"\bbert\b|transformer|roberta",
    "대규모 언어모델(LLM)": r"large language model|\bllms?\b|\bgpt[- ]?\d|chatgpt",
    "랜덤포레스트": r"random forest",
    "그래디언트 부스팅(XGBoost 등)": r"xgboost|gradient boost|lightgbm|catboost",
    "SVM": r"support vector",
    "딥러닝/신경망": r"deep learning|neural network|\blstm\b|\bcnn\b|딥러닝|신경망",
    "군집분석(Clustering)": r"cluster|k-means|군집",
    "SHAP/설명가능 AI": r"\bshap\b|explainab|interpretab|설명\s*가능",
    "교차검증(Cross-validation)": r"cross[- ]validation|교차\s*검증",
    "협업 필터링/행렬분해": r"collaborative filtering|matrix factori[sz]ation|협업\s*필터링",
    "그래프 신경망(GNN)": r"graph neural|\bgnn\b|graph convolution",
    "소셜 네트워크 분석(SNA)": r"social network analysis|centrality|community detection|ergm|네트워크\s*분석|중심성",
    "에이전트 기반 시뮬레이션": r"agent[- ]based",
    "자료포락분석(DEA)": r"data envelopment|\bdea\b",
    "AHP": r"analytic hierarchy|\bahp\b",
    "메타분석": r"meta[- ]analys",
    "인과 머신러닝(DML/Causal Forest)": r"double machine learning|causal forest|\bdml\b",
    "웹 크롤링/스크래핑": r"crawl|scrap(e|ing)|크롤링",
}

STOP = set("""a an the of and or in on for to with by from as at is are was were be been this that these those we our
their its it which using use used based study paper results result data model models analysis also than more
such can may between however both into not have has had there each other one two three over under new further""".split())


def _sentences(text):
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z가-힣\"(])", text) if 40 <= len(s.strip()) <= 400]


def _pick(sents, pattern, n):
    out = []
    for s in sents:
        if re.search(pattern, s, re.I) and s not in out:
            out.append(s)
        if len(out) >= n:
            break
    return out


def method_tags(text):
    low = text.lower()
    hits = [(name, len(re.findall(pat, low, re.I))) for name, pat in METHOD_DICT.items()]
    return [n for n, c in sorted(hits, key=lambda x: -x[1]) if c >= 2][:10]


def freq_keywords(text, n=10):
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower()) if w not in STOP]
    bigrams = Counter(f"{a} {b}" for a, b in zip(words, words[1:]) if a != b)
    uni = Counter(words)
    picks = [b for b, c in bigrams.most_common(30) if c >= 4][:6]
    picks += [u for u, _ in uni.most_common(40) if not any(u in p for p in picks)][: n - len(picks)]
    return picks[:n]


def extractive_analysis(doc, cfg):
    sec = lambda *roles: " ".join(s["text"] for s in doc["sections"] if s["role"] in roles)
    full = doc["full"]
    all_s = _sentences(full)
    abs_s = _sentences(doc["abstract"]) if doc["abstract"] else all_s[:8]
    intro_s = _sentences(sec("intro", "abstract", "front")) or all_s[:60]
    meth_s = _sentences(sec("method")) or all_s
    res_s = _sentences(sec("results", "discussion", "conclusion")) or all_s
    lim_only = _sentences(sec("limitations"))
    lim_s = _sentences(sec("conclusion", "discussion")) or all_s
    tags = method_tags(full)
    kw = doc["author_keywords"] or freq_keywords(full)
    interests = [t["name"] for t in cfg["topics"]]
    matched = [t["name"] for t in cfg["topics"]
               if any(k.lower() in full.lower() for k in t.get("keywords_en", []) + t.get("keywords_ko", []))]
    return {
        "tldr": abs_s[0] if abs_s else "",
        "summary": " ".join(abs_s[:6]),
        "research_questions": _pick(intro_s, r"research question|\brq\d?|we (ask|examine|investigate|explore|propose)|this (study|paper|article) (examines|investigates|explores|aims)|hypothes|연구\s*문제|가설", 4),
        "keywords": kw,
        "methodology": {
            "research_design": ", ".join(tags[:3]) or "논문에서 자동 탐지되지 않음",
            "data": " ".join(_pick(meth_s, r"\bdata(set)?\b|sample|collected|respondents|observations|corpus|panel|데이터|표본|수집", 3)),
            "techniques": tags,
            "analysis_procedure": " ".join(_pick(meth_s, r"we (estimate|employ|use|apply|conduct|train|test)|model|procedure|분석", 3)),
        },
        "key_findings": _pick(res_s, r"(we )?find|found|show|results? (indicate|suggest|show|reveal)|significant|evidence|outperform|결과", 5),
        "contributions": {
            "theoretical": " ".join(_pick(all_s, r"contribut\w* to .*(literature|theory)|theoretical", 2)),
            "practical": " ".join(_pick(all_s, r"managerial|practical|practitioner|implication|firms|marketers|시사점", 2)),
        },
        "issues": _pick(all_s, r"\bhowever\b|inconsistent|mixed (evidence|results|findings)|debate|controvers|contrary|unclear|remains", 4),
        "limitations": lim_only[:5] or _pick(lim_s, r"limitation|limited|caution|generali[sz]|bias|cannot|한계", 5),
        "future_research": _pick(all_s, r"future (research|work|stud)|further research|향후\s*연구", 4),
        "related_concepts": tags[:4] + kw[:4],
        "my_research_link": (f"관심 주제 중 '{', '.join(matched)}'와(과) 관련된 키워드가 본문에 등장합니다. "
                             f"이 논문의 방법론({', '.join(tags[:3]) or '본문 참고'})을 내 데이터에 적용할 수 있는지 검토해 보세요.")
        if matched else f"현재 관심 주제({', '.join(interests)})와 직접 겹치는 키워드는 적습니다. 방법론 관점에서 참고해 보세요.",
        "discussion_questions": [
            "이 연구의 핵심 가정이 한국 시장/기업 맥락에서도 성립할까?",
            f"사용된 방법({tags[0] if tags else '분석 방법'}) 대신 다른 방법을 쓴다면 결과가 달라질까?",
            "데이터의 대표성과 측정 방식에서 가장 취약한 지점은 어디일까?",
        ],
        "glossary": [],
        "_note": "LLM 엔진이 없어 규칙 기반으로 원문 문장을 발췌했습니다. 설정에서 Ollama/Gemini를 켜면 한국어 요약이 생성됩니다.",
    }


# ------------------------------------------------------------------ 5. 관련 논문 (OpenAlex)
def related_papers(title, keywords, cfg, n=6):
    def fmt(w):
        loc = w.get("primary_location") or {}
        return {
            "title": w.get("display_name") or "",
            "authors": ", ".join((a.get("author") or {}).get("display_name", "") for a in w.get("authorships", [])[:3]),
            "year": w.get("publication_year"), "venue": (loc.get("source") or {}).get("display_name") or "",
            "url": w.get("doi") or w.get("id"), "cited_by": w.get("cited_by_count", 0),
        }

    key = f"&api_key={cfg['openalex_api_key']}" if cfg.get("openalex_api_key") else ""
    found, self_info = [], None
    try:
        if title:
            res = get_json("https://api.openalex.org/works?per_page=1&search=" + urllib.parse.quote(title[:200]) + key)
            top = (res.get("results") or [None])[0]
            if top:
                a = set(re.findall(r"\w+", title.lower()))
                b = set(re.findall(r"\w+", (top.get("display_name") or "").lower()))
                if a and len(a & b) / len(a | b) > 0.6:
                    self_info = {"cited_by": top.get("cited_by_count", 0), "year": top.get("publication_year"),
                                 "venue": ((top.get("primary_location") or {}).get("source") or {}).get("display_name"),
                                 "doi": top.get("doi")}
                    ids = [i.rsplit("/", 1)[-1] for i in (top.get("related_works") or [])[:n]]
                    if ids:
                        rel = get_json("https://api.openalex.org/works?per_page=%d&filter=openalex:%s%s"
                                       % (n, "|".join(ids), key))
                        found = [fmt(w) for w in rel.get("results", [])]
        if len(found) < 3 and keywords:
            q = " ".join(keywords[:4])
            res = get_json("https://api.openalex.org/works?per_page=%d&sort=cited_by_count:desc&search=%s%s"
                           % (n, urllib.parse.quote(q), key))
            found += [fmt(w) for w in res.get("results", []) if (w.get("display_name") or "").lower() != (title or "").lower()]
    except Exception:
        pass
    return found[:n], self_info


# ------------------------------------------------------------------ 6. 전체 파이프라인
def _as_list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [x.strip(" -•") for x in re.split(r"\n+", v) if x.strip(" -•")]
    return []


def normalize(a):
    for k in ("research_questions", "keywords", "key_findings", "issues", "limitations", "future_research",
              "related_concepts", "discussion_questions"):
        a[k] = _as_list(a.get(k))
    m = a.get("methodology") or {}
    if isinstance(m, str):
        m = {"research_design": m}
    a["methodology"] = {"research_design": m.get("research_design", ""), "data": m.get("data", ""),
                        "techniques": _as_list(m.get("techniques")), "analysis_procedure": m.get("analysis_procedure", "")}
    c = a.get("contributions") or {}
    if isinstance(c, str):
        c = {"theoretical": c}
    a["contributions"] = {"theoretical": c.get("theoretical", ""), "practical": c.get("practical", "")}
    g = a.get("glossary") or []
    a["glossary"] = [x for x in g if isinstance(x, dict) and x.get("term")]
    for k in ("tldr", "summary", "my_research_link"):
        a[k] = str(a.get(k) or "")
    return a


def analyze(path, cfg, progress=lambda msg: None):
    progress("PDF 텍스트·목차 추출 중")
    doc = parse_pdf(path)
    engine = llm.pick_engine(cfg)
    interests = " / ".join(f"{t['name']}({', '.join(t.get('keywords_en', [])[:2])})" for t in cfg["topics"])

    analysis, used = None, "extractive"
    if engine != "extractive":
        progress(f"{llm.engine_label(cfg, engine)}로 논문 읽는 중 (몇 분 걸릴 수 있어요)")
        text = select_text(doc, llm.MAX_CHARS.get(engine, 20000))
        try:
            raw = llm.complete(cfg, SYSTEM, build_prompt(doc, text, interests), want_json=True,
                               schema=FIELDS_SCHEMA, engine=engine)
            analysis = llm.extract_json(raw)
            used = engine
        except Exception as e:
            progress(f"LLM 분석 실패({type(e).__name__}: {str(e)[:120]}) - 규칙 기반으로 전환")
    if analysis is None:
        analysis = extractive_analysis(doc, cfg)

    analysis = normalize(analysis)
    progress("OpenAlex에서 관련 논문 찾는 중")
    title = (analysis.get("title") or doc["title"] or "").strip()
    rel, self_info = related_papers(title, analysis["keywords"] or doc["author_keywords"], cfg)

    analysis["meta"] = {
        "title": title or doc["title"],
        "authors": analysis.get("authors") or doc["authors"] or "",
        "year": str(analysis.get("year") or (self_info or {}).get("year") or ""),
        "venue": analysis.get("venue") or (self_info or {}).get("venue") or "",
        "pages": doc["pages"],
        "cited_by": (self_info or {}).get("cited_by"),
        "doi": (self_info or {}).get("doi"),
    }
    analysis["toc"] = doc["toc"]
    analysis["detected_methods"] = method_tags(doc["full"])
    analysis["author_keywords"] = doc["author_keywords"]
    analysis["related_papers"] = rel
    analysis["engine"] = llm.engine_label(cfg, used)
    analysis["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return analysis
