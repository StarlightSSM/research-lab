"""My Little Lab 웹 서버.  실행: python app.py  ->  http://localhost:5050"""
import os as _os, sys as _sys
if _sys.stdout is None or _sys.stderr is None:  # pythonw(작업 스케줄러)로 실행된 경우
    _log = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "server.log"), "a", encoding="utf-8", buffering=1)
    _sys.stdout = _sys.stderr = _log

import json
import os
import threading
import time
import webbrowser

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from lab import jobs, llm, storage
from lab.config import UPLOAD_DIR, load_config, save_config

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024  # 업로드 최대 80MB
PORT = int(os.environ.get("LAB_PORT", 5050))


@app.get("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------- 대시보드 데이터
@app.get("/api/overview")
def overview():
    cfg = load_config()
    b = storage.latest_briefing()
    if b:
        b["content"] = json.loads(b["content"])
    return jsonify({
        "lab_name": cfg["lab_name"], "owner": cfg["owner"],
        "schedule": cfg["schedule"], "topics": [t["name"] for t in cfg["topics"]],
        "stats": storage.stats(), "briefing": b,
        "collecting": jobs.state["collecting"], "progress": jobs.state["progress"],
    })


@app.get("/api/items")
def items():
    a = request.args
    return jsonify(storage.list_items(kind=a.get("kind") or None, topic=a.get("topic") or None,
                                      q=a.get("q") or None, days=int(a.get("days", 14)),
                                      starred=a.get("starred") == "1"))


@app.patch("/api/items/<int:item_id>")
def patch_item(item_id):
    storage.update_item(item_id, **(request.json or {}))
    return jsonify({"ok": True})


@app.post("/api/collect")
def collect():
    if jobs.state["collecting"]:
        return jsonify({"status": "busy"})
    threading.Thread(target=jobs.run_collection, kwargs={"trigger": "manual"}, daemon=True).start()
    time.sleep(0.3)
    return jsonify({"status": "started"})


@app.get("/api/runs/last")
def last_run():
    return jsonify(storage.stats()["last_run"])


# ---------------------------------------------------------------- 논문 연구실
@app.get("/api/papers")
def papers():
    return jsonify(storage.list_papers())


@app.post("/api/papers")
def upload_paper():
    files = request.files.getlist("file")
    if not files:
        abort(400, "파일이 없습니다")
    ids = []
    for f in files:
        name = f.filename or "paper.pdf"
        if not name.lower().endswith(".pdf"):
            continue
        stored = os.path.join(UPLOAD_DIR, f"{int(time.time() * 1000)}_{secure_filename(name) or 'paper.pdf'}")
        f.save(stored)
        pid = storage.create_paper(name, stored)
        jobs.enqueue_paper(pid)
        ids.append(pid)
    if not ids:
        abort(400, "PDF 파일만 올릴 수 있어요")
    return jsonify({"ids": ids})


@app.post("/api/items/<int:item_id>/analyze")
def analyze_item(item_id):
    it = storage.get_item(item_id)
    if not it:
        abort(404)
    url = (it.get("extra") or {}).get("pdf_url")
    if not url:
        return jsonify({"error": "이 항목은 바로 받을 수 있는 PDF가 없어요. 직접 내려받아 업로드해 주세요."}), 400
    try:
        path = jobs.download_pdf(url, item_id)
    except Exception as e:
        return jsonify({"error": f"PDF 다운로드 실패: {e}"}), 400
    pid = storage.create_paper(it["title"][:120] + ".pdf", path, source_item=item_id)
    jobs.enqueue_paper(pid)
    return jsonify({"id": pid})


@app.get("/api/papers/<int:pid>")
def paper(pid):
    p = storage.get_paper(pid)
    if not p:
        abort(404)
    p.pop("stored_path", None)
    return jsonify(p)


@app.get("/api/papers/<int:pid>/pdf")
def paper_pdf(pid):
    p = storage.get_paper(pid)
    if not p or not p.get("report_path") or not os.path.exists(p["report_path"]):
        abort(404)
    return send_file(p["report_path"], as_attachment=request.args.get("dl") == "1",
                     download_name=os.path.basename(p["report_path"]), mimetype="application/pdf")


@app.post("/api/papers/<int:pid>/retry")
def retry_paper(pid):
    p = storage.get_paper(pid)
    if not p:
        abort(404)
    storage.update_paper(pid, status="queued", message="대기 중")
    jobs.enqueue_paper(pid)
    return jsonify({"ok": True})


@app.delete("/api/papers/<int:pid>")
def del_paper(pid):
    storage.delete_paper(pid)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 설정
@app.get("/api/config")
def get_config():
    cfg = load_config()
    cfg["_engines"] = llm.engine_status(cfg)
    return jsonify(cfg)


@app.put("/api/config")
def put_config():
    data = request.json or {}
    data.pop("_engines", None)
    cfg = save_config(data)
    cfg["_engines"] = llm.engine_status(cfg)
    return jsonify(cfg)


@app.post("/api/engine/test")
def engine_test():
    cfg = load_config()
    eng = llm.pick_engine(cfg)
    if eng == "extractive":
        return jsonify({"engine": llm.engine_label(cfg, eng), "reply": "LLM 없이 규칙 기반으로 동작합니다."})
    t = time.time()
    try:
        reply = llm.complete(cfg, "한국어로 짧게 답하세요.", "연구실 비서로서 한 문장으로 인사해 주세요.", engine=eng)
        return jsonify({"engine": llm.engine_label(cfg, eng), "reply": reply[:300], "sec": round(time.time() - t, 1)})
    except Exception as e:
        return jsonify({"engine": llm.engine_label(cfg, eng), "error": str(e)[:300]}), 500


if __name__ == "__main__":
    storage.init_db(reset_stale=True)
    load_config()
    jobs.start_background()
    print(f"\n  🔬 My Little Lab 실행 중 → http://localhost:{PORT}\n  (종료: Ctrl+C)\n")
    if os.environ.get("LAB_NO_BROWSER", "").strip() != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(host=os.environ.get("LAB_HOST", "127.0.0.1"), port=PORT, debug=False, threaded=True)
