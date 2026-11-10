"""Neo4j 연결 및 n10s 설치 확인."""
from neo4j import GraphDatabase
from maplerag.config import settings


def get_driver():
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def check_connection() -> dict:
    """연결·Neo4j 버전·n10s 설치 여부를 반환한다 (실패 시 예외)."""
    with get_driver() as driver:
        driver.verify_connectivity()
        with driver.session() as s:
            ver = s.run("CALL dbms.components() YIELD name, versions RETURN name, versions[0] AS v").single()
            n10s = s.run("SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'n10s.' RETURN count(*) AS c").single()["c"]
    return {"neo4j": f"{ver['name']} {ver['v']}", "n10s_procedures": n10s}
