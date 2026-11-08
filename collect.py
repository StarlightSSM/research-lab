"""서버 없이 수집만 실행.
  python collect.py              # 지금 바로 수집
  python collect.py --scheduled  # 오늘 예약 시각 이후 이미 수집했다면 건너뜀 (작업 스케줄러용)
"""
import os as _os, sys as _sys
if _sys.stdout is None or _sys.stderr is None:  # pythonw(작업 스케줄러)로 실행된 경우
    _log = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "collect.log"), "a", encoding="utf-8", buffering=1)
    _sys.stdout = _sys.stderr = _log

import sys
from datetime import datetime

from lab import jobs, storage
from lab.config import load_config

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    storage.init_db()
    if "--scheduled" in sys.argv:
        hhmm = load_config()["schedule"].get("time", "08:00")
        if storage.ran_since(datetime.now().strftime("%Y-%m-%d") + "T" + hhmm):
            print("오늘 예약 수집은 이미 완료됨 - 건너뜀")
            sys.exit(0)
    res = jobs.run_collection(trigger="task" if "--scheduled" in sys.argv else "cli")
    print(res)
    run = storage.stats()["last_run"]
    if run:
        print(run["log"])
