# AI-Powered SDLC Engineering Copilot — Multi-Agent Architecture

> **AI generates. Engineering tools validate. Humans govern.**

A working implementation of the *AI Engineer Hackathon* brief: a copilot that accepts an
application-development requirement and orchestrates the whole engineering lifecycle —

```
Requirement → Requirement analysis → User stories & AC → Technical design → [human approval]
→ Code generation → Test generation → Test execution → Static analysis → Security validation
→ AI code review → Quality gates → (Defect remediation → [human approval] → Revalidation)*
→ Traceability → Release-readiness assessment → [human approval]
```

Eight specialised agents produce typed artifacts; deterministic tools (pytest, Ruff, AST
analysis, Bandit, a secret scanner, optional pip-audit) validate them inside an isolated
workspace; configurable quality gates decide whether the change may progress; humans approve
the design, every AI-generated correction and the release. Everything is recorded — every
AI decision, tool call, gate evaluation, diff and human decision — and shown on an SDLC
dashboard.

![architecture](docs/architecture.png)

---
