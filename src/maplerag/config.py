"""환경설정: .env 에서 키를 읽는다. 키 값은 절대 출력하지 않는다."""
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    nexon_api_key: str = os.getenv("NEXON_API_KEY", "")
    langfuse_public_key: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    langfuse_secret_key: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    # 호스트 변수명은 LANGFUSE_HOST 를 기본으로 하되, 기존 .env 의 LANGFUSE_BASE_URL 도 허용
    langfuse_host: str = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL", "")
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7688")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    llm_model: str = "claude-sonnet-5-5"   # 샘플링 파라미터는 지정하지 않는다


def key_status(s: Settings) -> dict[str, bool]:
    """키가 설정됐는지만 True/False 로 반환 (값은 노출하지 않음)."""
    return {
        "ANTHROPIC_API_KEY": bool(s.anthropic_api_key),
        "NEXON_API_KEY": bool(s.nexon_api_key),
        "LANGFUSE_PUBLIC_KEY": bool(s.langfuse_public_key),
        "LANGFUSE_SECRET_KEY": bool(s.langfuse_secret_key),
        "LANGFUSE_HOST": bool(s.langfuse_host),
        "NEO4J_PASSWORD": bool(s.neo4j_password),
    }


settings = Settings()
