# Vex Quality — Adversarial Self-Review

Answer these five questions before every commit. Not optional.

---

## 1. If I were trying to break this, how would I do it?

What's the weakest point? Bad input? Race condition? Missing error path?
Resource exhaustion? Find at least one way. Fix it or document it.

---

## 2. What happens when this fails — not IF, when?

Every network call times out. Every file read hits permission denied.
Every lock acquisition blocks. Every parse gets malformed input.
What happens in THIS code when that occurs?

---

## 3. Does this read like a native speaker wrote it?

- Python: list comprehensions, context managers, `pathlib`, type hints
- Rust: `Result<T,E>`, `?` operator, idiomatic error propagation, `impl Trait`
- Bash: `set -euo pipefail`, `[[ ]]` not `[ ]`, subshells only when needed

Would a senior engineer in this language recognize the patterns?

---

## 4. Is there a test that proves this works?

One test. Even one. That demonstrates the happy path and one failure path.
If I can't write a test, I don't understand the code well enough to ship it.

---

## 5. What's the performance cost on the hot path?

If this runs on every daemon tick (300s), every HTTP request, or every
pipeline stage — what does it cost? CPU? Memory? I/O? Blocking or async?

---

*If any answer is "I don't know" — don't push. Find out first.*
