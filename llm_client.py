"""
LLM 호출 공통 래퍼: Anthropic(Claude), Gemini, Grok(xAI) 세 provider를 모두
지원한다. extractor/extract.py, extractor/extract_text.py,
relation_gen/generate.py, review_ui/app.py가 이 모듈을 공통으로 사용한다
(provider별 API 호출 코드를 4곳에 중복시키지 않기 위함).

API 키는 provider별로 다른 환경변수를 사용한다:
- provider=anthropic -> ANTHROPIC_API_KEY
- provider=gemini     -> GEMINI_API_KEY
- provider=grok       -> XAI_API_KEY

Grok(xAI)은 OpenAI 호환 REST API라 openai 패키지를 base_url만 바꿔서 그대로 쓴다.

사용법:
    from llm_client import call_llm, resolve_api_key, default_model, strip_json_fence

    api_key = resolve_api_key("gemini")
    raw, elapsed_seconds = call_llm(provider="gemini", api_key=api_key, model=default_model("gemini"),
                                     system=SYSTEM_PROMPT, user=user_prompt, max_tokens=1000)
    data = json.loads(strip_json_fence(raw))
"""
import os
import time

PROVIDERS = ("anthropic", "gemini", "grok")

MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 5.0

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "gemini": "gemini-3.6-flash",
    "grok": "grok-4-fast",
}

API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "grok": "XAI_API_KEY",
}

XAI_BASE_URL = "https://api.x.ai/v1"


def resolve_api_key(provider: str) -> str:
    return os.environ.get(API_KEY_ENV[provider])


def default_model(provider: str) -> str:
    return DEFAULT_MODELS[provider]


def call_llm(provider: str, api_key: str, model: str, system: str, user: str, max_tokens: int = 1500):
    """LLM을 호출하고 (응답 텍스트, 소요 시간(초)) 튜플을 반환한다.
    코드펜스 제거는 strip_json_fence로 별도 처리."""
    if provider == "anthropic":
        return _call_anthropic(api_key, model, system, user, max_tokens)
    if provider == "gemini":
        return _call_gemini(api_key, model, system, user, max_tokens)
    if provider == "grok":
        return _call_grok(api_key, model, system, user, max_tokens)
    raise ValueError(f"알 수 없는 provider: {provider!r} ({'/'.join(PROVIDERS)} 중 선택)")


def _with_retry(call_fn, is_retryable):
    """일시적 오류(과부하/rate limit 등)는 지수 백오프로 재시도하고,
    그 외 오류(인증 실패 등)는 즉시 올린다.
    반환값: (결과, 성공한 시도의 소요 시간(초)) — 재시도 대기 시간은 제외한,
    실제 모델 응답 시간만 측정 (모델별 속도 비교/거버넌스용)."""
    backoff = INITIAL_BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES + 1):
        try:
            start = time.time()
            result = call_fn()
            return result, time.time() - start
        except Exception as e:
            if attempt < MAX_RETRIES and is_retryable(e):
                print(f"[재시도 {attempt + 1}/{MAX_RETRIES}] 일시적 오류, {backoff:.0f}초 후 재시도: {e}")
                time.sleep(backoff)
                backoff *= 2
                continue
            raise


def _call_anthropic(api_key, model, system, user, max_tokens):
    from anthropic import Anthropic, APIStatusError, APIConnectionError

    client = Anthropic(api_key=api_key)

    def do_call():
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def is_retryable(e):
        if isinstance(e, APIConnectionError):
            return True
        return isinstance(e, APIStatusError) and e.status_code in (408, 429, 500, 502, 503, 504, 529)

    return _with_retry(do_call, is_retryable)


def _call_gemini(api_key, model, system, user, max_tokens):
    from google import genai
    from google.genai import types
    from google.genai.errors import ClientError, ServerError

    client = genai.Client(api_key=api_key)

    def do_call():
        response = client.models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        return response.text or ""

    def is_retryable(e):
        if isinstance(e, ServerError):
            return True
        return isinstance(e, ClientError) and getattr(e, "code", None) == 429

    return _with_retry(do_call, is_retryable)


def _call_grok(api_key, model, system, user, max_tokens):
    from openai import OpenAI, APIConnectionError, APIStatusError

    client = OpenAI(api_key=api_key, base_url=XAI_BASE_URL)

    def do_call():
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

    def is_retryable(e):
        if isinstance(e, APIConnectionError):
            return True
        return isinstance(e, APIStatusError) and e.status_code in (408, 429, 500, 502, 503, 504)

    return _with_retry(do_call, is_retryable)


def strip_json_fence(text: str) -> str:
    """모델이 ```json ... ``` 코드블록으로 감싸는 경우를 대비해 제거한다."""
    out = text.strip()
    if out.startswith("```"):
        out = out.strip("`")
        if out.startswith("json"):
            out = out[4:]
    return out.strip()


def provider_label(provider: str, model: str) -> str:
    """엔티티/관계에 붙일 출처 라벨. 예: 'gemini:gemini-3.5-flash-lite'.
    누가/무엇이 이 값을 만들었는지 추적(governance)하는 용도."""
    return f"{provider}:{model}"


def tag_generated_by(items: list, label: str) -> list:
    """엔티티/관계 딕셔너리 리스트에 출처 라벨을 일괄로 붙인다."""
    for item in items:
        item["generated_by"] = label
    return items


def tag_latency(items: list, elapsed_seconds) -> list:
    """엔티티/관계 딕셔너리 리스트에 이 결과를 만든 API 호출의 소요 시간(ms)을 붙인다.
    elapsed_seconds가 None이면(예: 호출 자체가 실패한 경우) 아무것도 붙이지 않는다."""
    if elapsed_seconds is None:
        return items
    for item in items:
        item["latency_ms"] = round(elapsed_seconds * 1000)
    return items
