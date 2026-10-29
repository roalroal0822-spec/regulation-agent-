"""세 앱이 공통으로 쓰는 웹 경로: 모델 설정, 상태 확인."""
import platform
import re
import sys

from .server import HttpError
from .llm import FAMILIES, LLM, LLMError


def _check_llm_values(b):
    """모델 주소는 http(s)만, API 키 환경변수 이름은 키·토큰용 이름만 허용한다
    (다른 환경변수 값이 모델 서버로 전송되는 것을 막음)."""
    url = (b.get("base_url") or "").strip()
    if url and not re.match(r"^https?://[^\s/]+", url):
        raise HttpError(400, "주소는 http:// 또는 https:// 로 시작해야 합니다")
    env = (b.get("api_key_env") or "").strip()
    if env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(KEY|TOKEN)[A-Za-z0-9_]*", env, re.I):
        raise HttpError(400, "API 키 환경변수 이름은 KEY 또는 TOKEN이 들어간 이름이어야 합니다(예: LLM_API_KEY)")


def register(app, cfg, llm, app_name, version):
    @app.get("/api/info")
    def info(req):
        return {"app": app_name, "version": version, "llm": llm.label,
                "llm_enabled": llm.enabled, "python": sys.version.split()[0],
                "os": platform.system(), "public": cfg.public}

    def no_public():
        if cfg.public:
            raise HttpError(403, "공개 시연판에서는 모델 설정을 바꿀 수 없습니다. 기관 PC에 설치해 쓰세요.")

    @app.get("/api/llm")
    def get_llm(req):
        c = dict(cfg["llm"])
        fam = llm.family
        return {"config": c, "label": llm.label, "family": fam["key"] if fam else "",
                "families": [{k: f[k] for k in ("key", "name", "maker", "ollama")} for f in FAMILIES]}

    @app.post("/api/llm/models")
    def list_models(req):
        """저장 전 입력값(연결 방식·주소)으로 서버의 모델 목록을 받아 온다."""
        no_public()
        b = req.json or {}
        _check_llm_values(b)
        c = dict(cfg["llm"], **{k: b[k] for k in ("provider", "base_url", "api_key_env") if b.get(k)})
        if c.get("provider") == "none":
            c["provider"] = "openai"
        try:
            return {"models": LLM(c, saved_base=(cfg["llm"].get("base_url") or "").rstrip("/")).models()}
        except LLMError as e:
            raise HttpError(502, str(e))

    @app.post("/api/llm")
    def set_llm(req):
        no_public()
        body = req.json
        if body.get("provider") not in ("none", "openai", "ollama"):
            raise HttpError(400, "provider 는 none, openai, ollama 중 하나여야 합니다")
        _check_llm_values(body)
        cfg.update_llm(body)
        return {"config": cfg["llm"], "label": llm.label}

    @app.post("/api/llm/test")
    def test_llm(req):
        no_public()
        return llm.health()


def register_about(app, root, about):
    """'서비스 구조·검증' 화면: 처리 단계·안전장치(앱이 정함) + 정확도 성적표·모델 교체 실측(검증결과 폴더에서 읽음).

    검증결과/scorecard.json   tools/eval.py가 만든 정확도 성적표(모델 없이 재현 가능)
    검증결과/model_check.json tools/verify_models.py가 실제 모델로 시험한 결과(기관 PC에서 실행)
    """
    import json

    def read(name):
        p = root / "검증결과" / name
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @app.get("/api/about")
    def get_about(req):
        return dict(about, scorecard=read("scorecard.json"), model_check=read("model_check.json"))
