"""실제 OpenAI 호출 경로를 한 번 태워보는 점검 스크립트.

테스트는 가짜 클라이언트로 `chat()` 경계까지만 확인한다. 진짜 API 가 기대한
모양으로 답하는지(JSON 모드, 응답 파싱)는 키가 있어야 알 수 있어서 따로 둔다.

    # 돈 안 씀 — 보낼 프롬프트와 근거를 그대로 찍어만 본다
    python scripts/check_llm.py --preview

    # 실제 호출 1회 (크레딧 필요)
    python scripts/check_llm.py

--preview 는 네트워크를 타지 않는다. 프롬프트가 마음에 드는지는 결제 전에
확인할 수 있어야 한다. 실제 호출은 gpt-4o 기준 1원 안팎이다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # 윈도우 cp949 콘솔에서 한글이 깨지지 않도록

from app.api.deps import get_explanation_service, get_service  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.explain import preview_prompt  # noqa: E402
from app.core.scoring import overall_risk  # noqa: E402

SAMPLE = ["쿠마딘", "아스피린", "은행잎추출물", "듣도보도못한것"]


def show_preview() -> int:
    """호출 없이, 보낼 내용을 그대로 보여준다."""
    service = get_service()
    resolution = service.find_interactions(SAMPLE)
    system, user = preview_prompt(
        resolution.interactions,
        overall_risk(resolution.interactions),
        resolution.unmatched,
    )
    print("=" * 70)
    print("SYSTEM (모델에게 주는 규칙)")
    print("=" * 70)
    print(system)
    print("=" * 70)
    print("USER (근거 — 여기 없는 사실은 모델도 쓸 수 없다)")
    print("=" * 70)
    print(user)
    print("=" * 70)
    print(f"대략 {(len(system) + len(user)) // 2:,} 토큰 안팎 (한글은 글자당 약 0.5토큰)")
    print("네트워크를 타지 않았습니다. 비용 0원.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preview",
        action="store_true",
        help="호출하지 않고 보낼 프롬프트만 출력한다 (비용 없음)",
    )
    args = parser.parse_args()

    if args.preview:
        return show_preview()

    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY 가 비어 있습니다. .env 에 키를 넣고 다시 실행하세요.")
        print("→ 키 없이도 /api/explain 은 template 설명으로 동작합니다.")
        return 1

    print(f"모델: {settings.openai_model}")
    print(f"입력: {', '.join(SAMPLE)}\n")

    result = get_explanation_service().explain(SAMPLE)

    print(f"[source] {result.source}" + (f" ({result.model})" if result.model else ""))
    if result.source != "llm":
        print("\n호출이 실패해 템플릿으로 내려앉았습니다. 위 로그의 예외를 확인하세요.")
        return 1

    print(f"\n[요약]\n{result.summary}\n")
    for item in result.interactions:
        print(f"[{item.severity}등급] {item.ingredient_a} × {item.ingredient_b}")
        print(f"  {item.plain}")
    print("\n[할 일]")
    for line in result.what_to_do:
        print(f"  - {line}")
    print(f"\n[면책] {result.disclaimer}")

    # 근거 밖으로 나가지 않았는지 눈으로 한 번 더 확인할 수 있게 원본을 같이 찍는다.
    pairs = {(i.ingredient_a, i.ingredient_b) for i in result.analysis.interactions}
    explained = {(i.ingredient_a, i.ingredient_b) for i in result.interactions}
    print(f"\n근거 쌍 {len(pairs)}개 / 설명된 쌍 {len(explained)}개", end=" ")
    print("— 일치" if pairs == explained else f"— 불일치! {pairs ^ explained}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
