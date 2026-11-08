"""무료로 쓸 수 있는 LLM 엔진 모음.

- ollama     : 내 PC에서 돌아가는 로컬 모델 (완전 무료, 오프라인, 논문이 외부로 나가지 않음)
- gemini     : Google AI Studio 무료 API 키 (https://aistudio.google.com/apikey)
- claude_cli : 설치된 Claude Code CLI (`claude -p`) - 사용 중인 Claude 구독 한도 안에서 동작
- extractive : LLM 없이 규칙 기반 (항상 동작하는 안전망)
"""
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def _post(url, payload, timeout, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _clean(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    return text.strip()


def extract_json(text):
    text = _clean(text)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise LLMError("모델 응답에서 JSON을 찾지 못했습니다")


# ------------------------------------------------------------ 엔진별 호출
def ollama_available(cfg):
    try:
        with urllib.request.urlopen(cfg["llm"]["ollama_url"].rstrip("/") + "/api/tags", timeout=3) as r:
            models = [m["name"] for m in json.loads(r.read()).get("models", [])]
        want = cfg["llm"]["ollama_model"]
        return any(m == want or m.split(":")[0] == want for m in models)
    except Exception:
        return False


def call_ollama(cfg, system, prompt, want_json=False, schema=None, timeout=2400, num_ctx=12288):
    payload = {
        "model": cfg["llm"]["ollama_model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "stream": False,
        "think": False,  # qwen3 등 '생각' 모델의 내부 추론 출력 끄기
        "options": {"temperature": 0.3, "num_ctx": num_ctx},
    }
    if want_json:
        payload["format"] = schema or "json"
    try:
        res = _post(cfg["llm"]["ollama_url"].rstrip("/") + "/api/chat", payload, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 400 and "think" in payload:  # 구버전 Ollama 호환
            payload.pop("think")
            res = _post(cfg["llm"]["ollama_url"].rstrip("/") + "/api/chat", payload, timeout)
        else:
            raise
    return _clean(res["message"]["content"])


def call_gemini(cfg, system, prompt, want_json=False, schema=None, timeout=300):
    key = cfg["llm"].get("gemini_api_key")
    if not key:
        raise LLMError("Gemini API 키가 없습니다")
    model = cfg["llm"].get("gemini_model") or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3},
    }
    if want_json:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    res = _post(url, payload, timeout, headers={"x-goog-api-key": key})
    try:
        return _clean("".join(p.get("text", "") for p in res["candidates"][0]["content"]["parts"]))
    except (KeyError, IndexError):
        raise LLMError(f"Gemini 응답 오류: {str(res)[:300]}")


def claude_available(cfg):
    return shutil.which(cfg["llm"].get("claude_cli") or "claude") is not None


def call_claude(cfg, system, prompt, want_json=False, schema=None, timeout=900):
    exe = shutil.which(cfg["llm"].get("claude_cli") or "claude")
    if not exe:
        raise LLMError("claude CLI를 찾지 못했습니다")
    full = system + "\n\n" + prompt + ("\n\n반드시 JSON 객체 하나만 출력하세요." if want_json else "")
    proc = subprocess.run([exe, "-p", "--output-format", "text"], input=full, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)
    if proc.returncode != 0:
        raise LLMError(f"claude CLI 오류: {proc.stderr[:300]}")
    return _clean(proc.stdout)


ENGINES = {"ollama": call_ollama, "gemini": call_gemini, "claude_cli": call_claude}

# 엔진별로 한 번에 넣을 논문 본문 최대 길이(문자)
MAX_CHARS = {"ollama": 16000, "gemini": 150000, "claude_cli": 120000}


def pick_engine(cfg):
    b = cfg["llm"].get("backend", "auto")
    if b != "auto":
        return b
    if ollama_available(cfg):
        return "ollama"
    if cfg["llm"].get("gemini_api_key"):
        return "gemini"
    if claude_available(cfg):
        return "claude_cli"
    return "extractive"


def engine_label(cfg, engine):
    return {
        "ollama": f"Ollama · {cfg['llm']['ollama_model']} (로컬)",
        "gemini": f"Gemini · {cfg['llm'].get('gemini_model')}",
        "claude_cli": "Claude Code CLI",
        "extractive": "규칙 기반 (LLM 미사용)",
    }.get(engine, engine)


def engine_status(cfg):
    return {
        "selected": pick_engine(cfg),
        "ollama": ollama_available(cfg),
        "gemini": bool(cfg["llm"].get("gemini_api_key")),
        "claude_cli": claude_available(cfg),
    }


def complete(cfg, system, prompt, want_json=False, schema=None, engine=None):
    engine = engine or pick_engine(cfg)
    if engine == "extractive":
        raise LLMError("LLM 엔진이 없습니다")
    fn = ENGINES[engine]
    return fn(cfg, system, prompt, want_json=want_json, schema=schema)
