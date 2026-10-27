"""Terminal demo. Run from project root:  python -m backend.demo_analyst"""

import json
from pathlib import Path

from backend.agents import analyst


def show(spec) -> None:
    print(f"\n=== {spec.title} ({spec.domain}) ===\n{spec.summary}\n")
    print("Requirements:")
    for r in spec.requirements:
        print(f"  {r.id} [{r.priority}/{r.source}] {r.text}")
    print("Acceptance criteria:")
    for a in spec.acceptance_criteria:
        print(f"  {a.id} -> {a.requirement_id}: GIVEN {a.given} WHEN {a.when} THEN {a.then}")
    print("Assumptions:")
    for s in spec.assumptions:
        print(f"  - {s}")


def ask_choice(n: int) -> str:
    """Accept only 0..n-1 or Enter (recommended). Re-ask on anything else."""
    while True:
        raw = input(f"  choice (0-{n - 1}, Enter = recommended): ").strip()
        if raw == "" or (raw.isdigit() and int(raw) < n):
            return raw
        print(f"  Invalid. Type a number from 0 to {n - 1}, or just press Enter.")


def main() -> None:
    prompt = input("Describe the feature: ")
    spec = analyst.analyze(prompt)
    show(spec)

    answers: dict[str, str] = {}
    if spec.questions:
        print("\nClarifying questions (press Enter for recommended):")
        for q in spec.questions:
            print(f"\n{q.id}: {q.question}\n  why: {q.why_it_matters}")
            for i, opt in enumerate(q.options):
                tag = "  (recommended)" if i == q.default_option else ""
                print(f"   [{i}] {opt}{tag}")
            answers[q.id] = ask_choice(len(q.options))
        spec = analyst.refine(spec, answers)
        show(spec)

    out = Path("output")
    out.mkdir(exist_ok=True)
    (out / "spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nSaved output/spec.json  status={spec.status}")
    print(json.dumps({"requirements": len(spec.requirements),
                      "criteria": len(spec.acceptance_criteria)}))


if __name__ == "__main__":
    main()
