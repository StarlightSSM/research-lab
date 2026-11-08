"""백그라운드 작업: 정기 수집 + 오늘의 브리핑 + 논문 분석 큐 + 매일 정해진 시간 스케줄러."""
import json
import os
import queue
import re
import threading
import time
import traceback
import urllib.request
from datetime import datetime

from . import analyzer, collectors, llm, report_pdf, storage
from .config import REPORT_DIR, UPLOAD_DIR, load_config

_collect_lock = threading.Lock()
state = {"collecting": False, "progress": ""}


# ------------------------------------------------------------------ 브리핑
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "object", "properties": {
            "topic": {"type": "string"}, "point": {"type": "string"}}, "required": ["topic", "point"]}},
        "must_read": {"type": "array", "items": {"type": "object", "properties": {
            "ref": {"type": "string"}, "reason": {"type": "string"}}, "required": ["ref", "reason"]}},
        "idea": {"type": "string"},
    },
    "required": ["greeting", "highlights", "must_read", "idea"],
}


def make_briefing(cfg, items):
    """새로 수집된 항목으로 '오늘의 브리핑' 생성 (dict)"""
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    refs = {}
    lines = []
    for kind, label in (("paper", "논문"), ("news", "뉴스"), ("issue", "이슈"), ("data", "데이터/코드")):
        group = [i for i in items if i["kind"] == kind][:25]
        if not group:
            continue
        lines.append(f"\n[{label}]")
        for n, it in enumerate(group, 1):
            ref = f"{kind[0].upper()}{n}"
            refs[ref] = it
            lines.append(f"{ref}. ({it['topic']}) {it['title'][:160]}")
    base = {
        "counts": counts,
        "greeting": "", "highlights": [], "must_read": [], "idea": "",
    }
    if not items:
        base["greeting"] = f"{cfg['owner']}님, 오늘은 새로 수집된 항목이 없어요. 관심 키워드를 조금 넓혀 보는 건 어떨까요?"
        return base, "규칙 기반"

    engine = llm.pick_engine(cfg)
    if engine != "extractive":
        prompt = (f"당신은 {cfg['owner']}님(경영학 계열 빅데이터응용 석사과정)의 연구 비서 '자비스'입니다. "
                  f"아래는 오늘 새로 수집된 논문·뉴스·이슈·데이터 목록입니다. 한국어로 아침 브리핑을 JSON으로 작성하세요.\n"
                  "- greeting: 오늘의 흐름을 요약하는 따뜻한 인사 1~2문장\n"
                  "- highlights: 주제별 핵심 동향 3~5개 (topic=주제명, point=한 문장)\n"
                  "- must_read: 오늘 꼭 볼 항목 3개 (ref=목록의 번호 예: P1, reason=석사 연구 관점의 이유 한 문장)\n"
                  "- idea: 오늘 항목들에서 얻을 수 있는 연구 아이디어 한 문장\n"
                  + "\n".join(lines))
        try:
            data = llm.extract_json(llm.complete(cfg, "간결하고 정확한 한국어 연구 비서", prompt,
                                                 want_json=True, schema=BRIEF_SCHEMA, engine=engine))
            mr = []
            for m in data.get("must_read") or []:
                ref = re.sub(r"[^A-Z0-9]", "", str(m.get("ref", "")).upper())
                if ref in refs:
                    it = refs[ref]
                    mr.append({"title": it["title"], "url": it["url"], "kind": it["kind"], "reason": m.get("reason", "")})
            base.update(greeting=data.get("greeting", ""), highlights=data.get("highlights") or [],
                        must_read=mr[:3], idea=data.get("idea", ""))
            return base, llm.engine_label(cfg, engine)
        except Exception as e:
            print("briefing LLM 실패:", e)

    # 규칙 기반 브리핑
    label = {"paper": "논문", "news": "뉴스", "issue": "이슈", "data": "데이터"}
    base["greeting"] = (f"좋은 하루예요, {cfg['owner']}님! 새 항목 " +
                        ", ".join(f"{label[k]} {v}건" for k, v in counts.items()) + "을 모아 두었어요.")
    for t in cfg["topics"]:
        tops = [i for i in items if i["topic"] == t["name"]]
        if tops:
            base["highlights"].append({"topic": t["name"], "point": f"새 항목 {len(tops)}건 · 대표: {tops[0]['title'][:80]}"})
    papers = sorted([i for i in items if i["kind"] == "paper"],
                    key=lambda i: -(i.get("extra", {}).get("cited_by") or 0))
    base["must_read"] = [{"title": p["title"], "url": p["url"], "kind": "paper", "reason": f"{p['topic']} · {p['source']}"}
                         for p in papers[:3]]
    return base, "규칙 기반"


# ------------------------------------------------------------------ 수집
def run_collection(trigger="manual"):
    if not _collect_lock.acquire(blocking=False):
        return {"status": "busy"}
    state["collecting"] = True
    run_id = storage.start_run()
    log = [f"[{storage.now()}] 수집 시작 ({trigger})"]
    try:
        cfg = load_config()

        def _log(msg):
            log.append(msg)
            state["progress"] = msg.strip()

        items, errors = collectors.collect_all(cfg, log=_log)
        new_items = storage.upsert_items(items)
        added = len(new_items)
        today = datetime.now().strftime("%Y-%m-%d")
        prev = storage.latest_briefing()
        if new_items or not prev or prev["day"] != today:  # 새 항목 없으면 오늘 브리핑을 덮어쓰지 않음
            _log(f"새 항목 {added}건 저장. 브리핑 작성 중...")
            # 하루에 여러 번 수집해도 브리핑은 '오늘 들어온 전체'를 기준으로
            brief, engine = make_briefing(cfg, storage.items_since(today))
            storage.save_briefing(today, json.dumps(brief, ensure_ascii=False), engine)
        status = "partial" if errors else "done"
        log.append(f"[{storage.now()}] 완료: 새 항목 {added}건, 오류 {len(errors)}건")
        storage.finish_run(run_id, status, "\n".join(log), added)
        return {"status": status, "added": added, "errors": errors}
    except Exception as e:
        log.append(traceback.format_exc())
        storage.finish_run(run_id, "error", "\n".join(log), 0)
        return {"status": "error", "error": str(e)}
    finally:
        state["collecting"] = False
        state["progress"] = ""
        _collect_lock.release()


# ------------------------------------------------------------------ 논문 분석 큐
_paper_q = queue.Queue()


def enqueue_paper(pid):
    _paper_q.put(pid)


def _safe_name(s):
    return re.sub(r"[^\w가-힣\-]+", "_", s)[:60].strip("_") or "paper"


def _process_paper(pid):
    p = storage.get_paper(pid)
    if not p:
        return
    cfg = load_config()
    storage.update_paper(pid, status="running", message="분석 시작")
    try:
        a = analyzer.analyze(p["stored_path"], cfg, progress=lambda m: storage.update_paper(pid, message=m))
        storage.update_paper(pid, message="PDF 리포트 작성 중")
        title = a["meta"].get("title") or p["filename"]
        out = os.path.join(REPORT_DIR, f"{pid:03d}_{_safe_name(title)}_brief.pdf")
        report_pdf.build_report(a, out, lab_name=cfg.get("lab_name", "My Little Lab"))
        storage.update_paper(pid, status="done", message="완료", analysis=a, report_path=out,
                             title=title, engine=a.get("engine", ""))
    except Exception as e:
        traceback.print_exc()
        storage.update_paper(pid, status="error", message=f"{type(e).__name__}: {str(e)[:200]}")


def _paper_worker():
    while True:
        pid = _paper_q.get()
        try:
            _process_paper(pid)
        finally:
            _paper_q.task_done()


def download_pdf(url, item_id):
    """수집된 논문(arXiv/OA)의 PDF를 내려받아 분석 큐에 넣기"""
    req = urllib.request.Request(url, headers={"User-Agent": collectors.UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(60 * 1024 * 1024)
    if not data.startswith(b"%PDF"):
        raise ValueError("PDF 파일이 아닙니다 (출판사 페이지일 수 있어요. 직접 내려받아 업로드해 주세요)")
    path = os.path.join(UPLOAD_DIR, f"item{item_id}_{int(time.time())}.pdf")
    with open(path, "wb") as f:
        f.write(data)
    return path


# ------------------------------------------------------------------ 스케줄러
def _scheduler():
    while True:
        try:
            cfg = load_config()
            sch = cfg.get("schedule", {})
            if sch.get("enabled"):
                now = datetime.now()
                hhmm = sch.get("time", "08:00")
                slot = now.strftime("%Y-%m-%d") + "T" + hhmm  # 오늘 예약 시각
                # 예약 시각이 지났는데 그 이후 수집이 없으면 실행 (PC를 늦게 켜도 따라잡음)
                if now.strftime("%H:%M") >= hhmm and not storage.ran_since(slot) and not state["collecting"]:
                    run_collection(trigger="schedule")
        except Exception:
            traceback.print_exc()
        time.sleep(30)


def start_background():
    threading.Thread(target=_paper_worker, daemon=True, name="paper-worker").start()
    threading.Thread(target=_scheduler, daemon=True, name="scheduler").start()
