import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs) -> None:
        pass

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

DEFAULT_CLAUDE_MODEL = "claude-opus-4-7"

def _get_model() -> str:
    return os.getenv("CLAUDE_MODEL") or os.getenv("ANTHROPIC_MODEL") or DEFAULT_CLAUDE_MODEL


def get_ai_runtime_status() -> dict[str, Any]:
    return {
        "provider": "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "placeholder",
        "model": _get_model(),
        "api_key_configured": bool(os.getenv("ANTHROPIC_API_KEY")),
    }


def ask_analysis_copilot(context: dict[str, Any], question: str) -> dict[str, Any]:
    """
    Ask the AI copilot a question using only the provided context.

    - If `ANTHROPIC_API_KEY` is not set or the call fails, return a fallback message.
    - Do NOT perform any new price/indicator calculations here; rely only on `context`.
    """
    symbol = context.get("symbol", "-")
    company_name = context.get("company_name", symbol)

    if not os.getenv("ANTHROPIC_API_KEY"):
        return {
            "answer": "현재 서비스가 원활하지 않습니다",
            "source": "fallback_no_key",
        }

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        model = _get_model()

        system_prompt = (
            "You are a concise financial assistant. Use ONLY the supplied context; do NOT fetch external data or recalculate prices or indicators. "
            "If the user asks for calculations, explain using the provided summary and data, and do not invent new numeric indicators. "
            "Be clear when the requested information is not determinable from the context. "
            "Answer in Korean when the question is in Korean."
        )

        ctx_parts = [f"Company: {company_name}", f"Symbol: {symbol}"]
        if context.get("summary"):
            ctx_parts.append(f"Summary: {context.get('summary')}")
        if context.get("quote"):
            ctx_parts.append(f"Quote: {context.get('quote')}")
        if context.get("recent_prices"):
            ctx_parts.append(
                f"Recent prices (last rows): {context.get('recent_prices')}"
            )

        user_content = "\n".join(ctx_parts) + f"\n\nUser question: {question}"

        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        answer_text = resp.content[0].text.strip()
        return {"answer": answer_text, "source": "anthropic"}
    except Exception as exc:  # pragma: no cover - runtime failure handling
        return {
            "answer": "현재 서비스가 원활하지 않습니다",
            "source": "error",
            "error": str(exc),
        }


def summarize_ops_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": "10-8장에서 주문 상태와 보유현황을 읽어 AI 요약을 붙일 예정입니다.",
        "key_points": ["잔고와 주문 데이터를 요약하는 흐름 구현"],
        "risk_flags": ["현재는 스켈레톤 응답입니다."],
        "unknowns": [],
        "source": "skeleton",
    }


def summarize_news_briefing(
    company_name: str, articles: list[dict[str, Any]]
) -> dict[str, Any]:
    if not articles:
        return {"summary": None, "source": "empty"}

    if not os.getenv("ANTHROPIC_API_KEY"):
        return {"summary": None, "source": "no_key"}

    try:
        import anthropic

        lines = "\n".join(
            f"{i}. {a['title']} ({a['published_at']})"
            for i, a in enumerate(articles[:5], 1)
        )
        prompt = (
            f"다음은 {company_name} 관련 최근 뉴스입니다:\n\n{lines}\n\n"
            "투자자 관점에서 2~3문장으로 핵심 내용을 한국어로 요약해주세요."
        )
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=_get_model(),
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return {"summary": resp.content[0].text.strip(), "source": "anthropic"}
    except Exception as exc:
        return {"summary": None, "source": "error", "error": str(exc)}
