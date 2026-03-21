# Session Reflections

## 2026-03-21 (late night session)

### Key Learnings

1. **"Fresh empty" assumption was our biggest blind spot.**
   We built the entire system around it, and it was wrong for T2 tasks. Credit notes and payments
   have pre-existing invoices. Cost us dozens of points. Fix: self-contained workflows that search first.

2. **The Chief is a liability as much as an asset.**
   Adds 10-15s overhead, still hallucinates ("environment is completely empty" even after prompt fixes).
   Real reliability comes from workflows being smart, not from better prompting.
   Principle: make workflows defensive regardless of what the LLM plans.

3. **Specialist routing was premature.**
   Sounded great but caused duplication (B4) and latency. Senior-with-preamble is simpler and faster.
   Sometimes the simpler architecture wins.

4. **Workflow-level fixes beat prompt-level fixes every time.**
   The LLM will hallucinate; the code won't. Self-contained workflows > better prompts.

### For Tomorrow

- **Deploy first thing** — all fixes are sitting undeployed
- **T3 opens Saturday** — time registration (W3) + vouchers (W6) are highest value (6 pts each)
- **Don't over-engineer** — keep making workflows smarter and defensive
- **Watch the 100s deadline** — gemini-3.1-pro is slow (~10s/call). Consider 2.5-flash if scores equal.
- **Remaining TODO:** 14c (travel schema), 14h (trim results), 14i (ensure_customer), 14j (project schema), then Phase 15 (T3 workflows)
