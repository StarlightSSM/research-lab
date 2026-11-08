"""무료(키 불필요) 공개 API/RSS에서 논문·뉴스·이슈·데이터를 수집합니다.

새 소스를 추가하려면: (cfg, topic) -> list[dict] 함수를 만들고 SOURCES에 등록하면 됩니다.
항목 dict 필드: kind, source, topic, title, url, summary, authors, published, extra
"""
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape

UA = "MyLittleLab/1.0 (personal research dashboard)"


def http_get(url, timeout=20, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url, **kw):
    return json.loads(http_get(url, **kw).decode("utf-8"))


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", unescape(s)).strip()


def _date(s):
    """여러 형식의 날짜 문자열 -> YYYY-MM-DD"""
    if not s:
        return ""
    try:
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s[:10]


def _since(cfg):
    return datetime.now() - timedelta(days=int(cfg["limits"].get("days_back", 14)))


# ---------------------------------------------------------------- 논문
def arxiv(cfg, topic):
    kws = topic.get("keywords_en") or []
    if not kws:
        return []
    q = " OR ".join(f'abs:"{k}"' for k in kws)
    url = ("http://export.arxiv.org/api/query?" + urllib.parse.urlencode({
        "search_query": q, "sortBy": "submittedDate", "sortOrder": "descending",
        "max_results": cfg["limits"]["per_query"]}))
    root = ET.fromstring(http_get(url, timeout=30))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns):
        link = e.findtext("a:id", "", ns).strip()
        pdf = next((l.get("href") for l in e.findall("a:link", ns) if l.get("title") == "pdf"), "")
        cats = [c.get("term") for c in e.findall("a:category", ns)]
        out.append({
            "kind": "paper", "source": "arXiv", "topic": topic["name"],
            "title": re.sub(r"\s+", " ", e.findtext("a:title", "", ns)),
            "url": link,
            "summary": re.sub(r"\s+", " ", e.findtext("a:summary", "", ns)).strip(),
            "authors": ", ".join(a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)[:6]),
            "published": _date(e.findtext("a:published", "", ns)),
            "extra": {"pdf_url": pdf.replace("http://", "https://"), "categories": cats[:4], "venue": "arXiv preprint"},
        })
    time.sleep(3)  # arXiv 이용 규칙: 요청 간 3초
    return out


def _openalex_abstract(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def openalex(cfg, topic):
    out = []
    since = _since(cfg).strftime("%Y-%m-%d")
    for kw in (topic.get("keywords_en") or [])[:2]:
        params = {
            "search": kw, "per_page": cfg["limits"]["per_query"],
            "filter": f"from_publication_date:{since},type:article",
            "sort": "relevance_score:desc",
        }
        if cfg.get("openalex_api_key"):
            params["api_key"] = cfg["openalex_api_key"]
        data = get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
        for w in data.get("results", []):
            loc = w.get("primary_location") or {}
            src = (loc.get("source") or {}).get("display_name") or ""
            oa = w.get("best_oa_location") or {}
            out.append({
                "kind": "paper", "source": "OpenAlex", "topic": topic["name"],
                "title": w.get("display_name") or w.get("title") or "",
                "url": w.get("doi") or w.get("id"),
                "summary": _openalex_abstract(w.get("abstract_inverted_index"))[:1200],
                "authors": ", ".join((a.get("author") or {}).get("display_name", "") for a in w.get("authorships", [])[:6]),
                "published": w.get("publication_date", ""),
                "extra": {"venue": src, "cited_by": w.get("cited_by_count", 0),
                          "pdf_url": oa.get("pdf_url") or "", "openalex_id": w.get("id")},
            })
    return out


# ---------------------------------------------------------------- 뉴스
def _parse_feed(raw):
    """RSS 2.0 / Atom 공용 파서"""
    root = ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        items.append({
            "title": it.findtext("title", ""), "url": it.findtext("link", ""),
            "summary": strip_html(it.findtext("description", "")),
            "published": _date(it.findtext("pubDate", "")),
            "source_name": it.findtext("source", ""),
        })
    atom = "{http://www.w3.org/2005/Atom}"
    for e in root.iter(atom + "entry"):
        link = e.find(atom + "link")
        items.append({
            "title": e.findtext(atom + "title", ""),
            "url": link.get("href") if link is not None else "",
            "summary": strip_html(e.findtext(atom + "content", "") or e.findtext(atom + "summary", "")),
            "published": _date(e.findtext(atom + "updated", "") or e.findtext(atom + "published", "")),
            "source_name": "",
        })
    return items


def _google_news(cfg, topic, lang):
    kws = topic.get("keywords_ko" if lang == "ko" else "keywords_en") or []
    out = []
    loc = {"ko": ("ko", "KR", "KR:ko"), "en": ("en-US", "US", "US:en")}[lang]
    for kw in kws[:2]:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": f"{kw} when:{cfg['limits'].get('days_back', 14)}d", "hl": loc[0], "gl": loc[1], "ceid": loc[2]})
        for it in _parse_feed(http_get(url))[: cfg["limits"]["per_query"]]:
            title = it["title"]
            press = it["source_name"]
            if press and title.endswith(" - " + press):
                title = title[: -(len(press) + 3)]
            out.append({
                "kind": "news", "source": "Google 뉴스" + (" (KR)" if lang == "ko" else " (EN)"),
                "topic": topic["name"], "title": title, "url": it["url"],
                "summary": "", "authors": press, "published": it["published"],
                "extra": {"press": press, "query": kw},
            })
    return out


def google_news_ko(cfg, topic):
    return _google_news(cfg, topic, "ko")


def google_news_en(cfg, topic):
    return _google_news(cfg, topic, "en")


_geeknews_cache = {}


def geeknews(cfg, topic):
    """GeekNews(한국 개발/IT 뉴스) 최신 피드에서 키워드가 포함된 글만 필터"""
    key = datetime.now().strftime("%Y%m%d%H")
    if key not in _geeknews_cache:
        _geeknews_cache.clear()
        _geeknews_cache[key] = _parse_feed(http_get("https://news.hada.io/rss/news"))
    words = [w.lower() for w in (topic.get("keywords_ko") or []) + (topic.get("keywords_en") or [])]
    # 키워드를 단어 단위로도 매칭 (예: '감성분석', 'sentiment')
    tokens = set(words) | {t for w in words for t in w.split() if len(t) >= 4}
    out = []
    for it in _geeknews_cache[key]:
        text = (it["title"] + " " + it["summary"]).lower()
        if any(t in text for t in tokens):
            out.append({
                "kind": "issue", "source": "GeekNews", "topic": topic["name"],
                "title": it["title"], "url": it["url"], "summary": it["summary"][:400],
                "authors": "", "published": it["published"], "extra": {},
            })
    return out[: cfg["limits"]["per_query"]]


# ---------------------------------------------------------------- 이슈/커뮤니티
def hackernews(cfg, topic):
    ts = int(_since(cfg).timestamp())
    out = []
    for kw in (topic.get("keywords_en") or [])[:2]:
        url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode(
            {"query": kw, "tags": "story", "numericFilters": f"created_at_i>{ts},points>=5",
             "hitsPerPage": cfg["limits"]["per_query"]})
        for h in get_json(url).get("hits", []):
            hn_url = f"https://news.ycombinator.com/item?id={h['objectID']}"
            out.append({
                "kind": "issue", "source": "Hacker News", "topic": topic["name"],
                "title": h.get("title") or "", "url": h.get("url") or hn_url,
                "summary": "", "authors": h.get("author", ""),
                "published": _date(h.get("created_at", "")),
                "extra": {"points": h.get("points", 0), "comments": h.get("num_comments", 0), "discussion": hn_url},
            })
    return out


# ---------------------------------------------------------------- 데이터/코드
def github(cfg, topic):
    kws = topic.get("keywords_en") or []
    if not kws:
        return []
    since = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": f"{kws[0]} created:>{since}", "sort": "stars", "order": "desc", "per_page": cfg["limits"]["per_query"]})
    data = get_json(url, headers={"Accept": "application/vnd.github+json"})
    return [{
        "kind": "data", "source": "GitHub", "topic": topic["name"],
        "title": r["full_name"], "url": r["html_url"], "summary": r.get("description") or "",
        "authors": (r.get("owner") or {}).get("login", ""), "published": _date(r.get("created_at", "")),
        "extra": {"stars": r.get("stargazers_count", 0), "language": r.get("language") or ""},
    } for r in data.get("items", [])]


def huggingface(cfg, topic):
    out = []
    for kw in (topic.get("data_keywords") or [])[:2]:
        url = "https://huggingface.co/api/datasets?" + urllib.parse.urlencode(
            {"search": kw, "sort": "downloads", "direction": -1, "limit": cfg["limits"]["per_query"]})
        for d in get_json(url):
            desc = (d.get("cardData") or {}).get("pretty_name") or d.get("description") or ""
            out.append({
                "kind": "data", "source": "HF Datasets", "topic": topic["name"],
                "title": d["id"], "url": f"https://huggingface.co/datasets/{d['id']}",
                "summary": strip_html(desc)[:300], "authors": d.get("author", ""),
                "published": _date(d.get("lastModified", "")),
                "extra": {"downloads": d.get("downloads", 0), "likes": d.get("likes", 0)},
            })
    return out


SOURCES = {
    "arxiv": arxiv,
    "openalex": openalex,
    "google_news_ko": google_news_ko,
    "google_news_en": google_news_en,
    "geeknews": geeknews,
    "hackernews": hackernews,
    "github": github,
    "huggingface": huggingface,
}


def collect_all(cfg, log=print):
    """모든 관심 주제 × 활성 소스 수집. (items, errors) 반환"""
    items, errors = [], []
    for name, fn in SOURCES.items():
        if not cfg["sources"].get(name):
            continue
        for topic in cfg["topics"]:
            try:
                got = fn(cfg, topic)
                items.extend(got)
                log(f"  [{name}] {topic['name']}: {len(got)}건")
            except Exception as e:  # 한 소스가 실패해도 나머지는 계속
                msg = f"  [{name}] {topic['name']}: 실패 - {type(e).__name__}: {e}"
                errors.append(msg)
                log(msg)
    return items, errors
