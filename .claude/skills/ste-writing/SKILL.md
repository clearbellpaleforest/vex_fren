---
name: ste-writing
description: Write technical documentation (READMEs, API docs, error messages, PR descriptions, release notes, comments) in ASD-STE100 Simplified Technical English to remove AI slop. Use when asked to write docs, make writing clear, enforce a controlled style, or produce technical text that reads human. Two modes: strict and flavored.
---

# STE Writing

Write prose in ASD-STE100 Simplified Technical English. Removes the padding, hedging, and marketing cruft that LLMs inject into technical writing. Cuts slop by 50-74% (tested across Claude and GPT).

Applies to: documentation, READMEs, PR text, error messages, release notes, comments.
Does NOT apply to: code, identifiers, command syntax, creative writing, conversational text.

## Rules

**Words**
- One name for one thing. Don't call the same item by two names.
- Short common word: start (not commence), use (not utilize), help (not facilitate), make sure (not ensure), before (not prior to), about (not regarding), show (not demonstrate), also (not additionally).
- No marketing adjectives: seamless, robust, powerful, cutting-edge, effortless, revolutionary.
- American spelling.

**Verbs**
- Active voice. "The parser reads the file" not "the file is read by the parser."
- Use a verb for an action. "Analyze the log" not "perform an analysis of the log."
- No stacked auxiliaries. Not "it is important to note that this may help." Write "this helps."

**Sentences**
- One instruction per sentence. Max 20 words (instruction), max 25 (descriptive).
- No contractions. Use articles: a, an, the.

**Punctuation**
- No semicolons. Write two sentences.
- No em-dashes.

**Structure**
- One topic per paragraph. Max 6 sentences.
- For steps: numbered list, one action per item, imperative form.
- Condition before its command.

## Modes

- **strict** — procedures, runbooks, error messages, safety text. Apply every rule.
- **flavored** — general prose (READMEs, docs). Apply sentence/paragraph/active-voice rules. Relax dictionary constraints so the text keeps range.

## Self-lint (run before returning)

1. Any sentence over 20 words? Split it.
2. Any semicolon? Replace with period.
3. Any contraction? Expand it.
4. Any passive voice with known actor? Make it active.
5. Any nominalization ("perform an analysis") or phrasal verb ("spin up")? Replace with plain verb.
6. Same thing named two ways? Pick one name.

Write only the requested text. No preamble, no closing remarks.
