"""캐릭터 조회: uv run python -m maplerag.character.run 닉네임 "질문"   (LLM 호출 없음, 캐시 없으면 Open API 최대 3회)"""
import sys

from maplerag.character.lookup import ask_character


def main():
    nick, q = sys.argv[1], " ".join(sys.argv[2:]) or "기본 정보 알려줘"
    r = ask_character(nick, q)
    print(r.answer)
    print(f"[임시 트리플 {r.triples}개 / 이번 실제 API 호출 {r.live_calls}회 / 출처 {len(r.sources)}건] {r.attribution}")
    for u in r.sources:
        print("  -", u)


if __name__ == "__main__":
    main()
