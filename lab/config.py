"""설정 파일(config.json) 로드/저장. 대시보드의 '설정' 화면에서도 수정할 수 있습니다."""
import copy
import json
import os
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
REPORT_DIR = os.path.join(DATA_DIR, "reports")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

for _d in (DATA_DIR, UPLOAD_DIR, REPORT_DIR):
    os.makedirs(_d, exist_ok=True)

# 경영학 계열 빅데이터응용 석사생을 가정한 기본 관심 분야 (설정 화면에서 자유롭게 수정)
DEFAULT_CONFIG = {
    "owner": "수민",
    "lab_name": "Sumin's Little Lab",
    "schedule": {"enabled": True, "time": "08:00"},
    "topics": [
        {
            "name": "추천시스템 & 고객행동",
            "keywords_en": ["recommender system", "consumer behavior machine learning"],
            "keywords_ko": ["추천시스템", "고객 행동 데이터"],
            "data_keywords": ["recommendation", "ecommerce"],
        },
        {
            "name": "생성형 AI의 경영 활용",
            "keywords_en": ["large language models business", "generative AI marketing"],
            "keywords_ko": ["생성형 AI 기업", "LLM 비즈니스"],
            "data_keywords": ["marketing", "customer"],
        },
        {
            "name": "텍스트마이닝 & 감성분석",
            "keywords_en": ["online reviews text mining", "sentiment analysis"],
            "keywords_ko": ["텍스트마이닝", "감성분석"],
            "data_keywords": ["reviews", "sentiment"],
        },
        {
            "name": "네트워크 사이언스",
            "keywords_en": ["social network analysis", "network science"],
            "keywords_ko": ["소셜 네트워크 분석", "네트워크 과학"],
            "data_keywords": ["graph", "network"],
        },
    ],
    "sources": {
        "arxiv": True,
        "openalex": True,
        "google_news_ko": True,
        "google_news_en": True,
        "geeknews": True,
        "hackernews": True,
        "github": True,
        "huggingface": True,
    },
    "limits": {"per_query": 5, "days_back": 14},
    "llm": {
        # auto: ollama -> gemini -> claude_cli -> extractive 순으로 사용 가능한 엔진 선택
        "backend": "auto",
        "ollama_url": "http://localhost:11434",
        "ollama_model": "qwen3:8b",
        "gemini_api_key": "",
        "gemini_model": "gemini-2.5-flash",
        "claude_cli": "claude",
    },
    "openalex_api_key": "",
}

_lock = threading.Lock()


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    with _lock:
        if not os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
            return copy.deepcopy(DEFAULT_CONFIG)
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        return _merge(DEFAULT_CONFIG, user)


def save_config(cfg):
    merged = _merge(DEFAULT_CONFIG, cfg)
    with _lock:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    return merged
