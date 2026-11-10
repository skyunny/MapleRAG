def main() -> None:
    """연결 확인: uv run maplerag"""
    from maplerag.config import settings, key_status
    print("키 설정 여부:", key_status(settings))
    from maplerag.graph.neo4j_client import check_connection
    try:
        print("Neo4j 연결 결과:", check_connection())
    except Exception as e:
        print(f"Neo4j 연결 실패: {type(e).__name__}: {e}")
