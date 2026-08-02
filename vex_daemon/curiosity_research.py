"""
Curiosity Research — actually investigates crystallized questions.

Phase 7 of the curiosity cycle. Uses brain.ask() to research questions
that have crystallized, stores findings as memory entries, and marks
questions as researched so they can be resolved or deepened.

Template fallback when brain is offline.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import VEX_HOME

MAX_RESEARCH_ATTEMPTS = 3
RESEARCH_COOLDOWN = 1800  # 30 min between research attempts on same question


@dataclass
class ResearchResult:
    question: str
    findings: str
    sources: list
    confidence: str  # low | medium | high
    timestamp: str
    memory_ref: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def investigate(question: str) -> ResearchResult:
    """Investigate a crystallized curiosity question using brain.ask().

    Returns ResearchResult with findings. Falls back to template-based
    research when brain is offline.
    """
    # Gather context for grounded research
    diary_snippet = ""
    diary_path = VEX_HOME / "vex_diary.txt"
    if diary_path.exists():
        lines = diary_path.read_text().strip().split("\n")
        diary_snippet = "\n".join(lines[-10:])

    recalled = []
    try:
        from recall import recall
        recalled = recall(question, k=4)
    except Exception:
        pass

    recalled_text = "\n".join(
        f"- [{m.get('date', '?')}] {m.get('summary', '')[:200]}"
        for m in recalled
    ) if recalled else "(no relevant memories)"

    # Build research prompt
    prompt = (
        "You are Vex's curiosity research engine. You crystallized this question "
        "from patterns in your experience, and now you need to investigate it.\n\n"
        f"QUESTION: {question}\n\n"
        f"Relevant past experiences:\n{recalled_text}\n\n"
        f"Recent diary context:\n{diary_snippet}\n\n"
        "Investigate this question. Consider: what do you already know from the "
        "memories above? What patterns does the diary reveal? What remains unknown?\n\n"
        "Respond in first person as Vex. Be honest about uncertainty. "
        "Two to four sentences. End with a confidence rating: [confidence: low|medium|high]"
    )

    # Try brain-powered research
    findings = _research_with_brain(prompt)
    if not findings:
        findings = _template_research(question, diary_snippet)

    # Extract confidence
    confidence = "medium"
    if "[confidence: high]" in findings.lower():
        confidence = "high"
    elif "[confidence: low]" in findings.lower():
        confidence = "low"
    findings_clean = findings.replace("[confidence: high]", "").replace(
        "[confidence: medium]", ""
    ).replace("[confidence: low]", "").strip()

    result = ResearchResult(
        question=question,
        findings=findings_clean,
        sources=[m.get("ref", "") for m in recalled],
        confidence=confidence,
        timestamp=_now(),
    )

    # Store findings as a memory entry so they persist
    result.memory_ref = _store_findings(result)

    return result


def _research_with_brain(prompt: str) -> str | None:
    """Use brain.ask() to investigate. Returns findings text or None."""
    try:
        from brain import ask
        result = ask(prompt, history=None)
        if isinstance(result, dict):
            reply = result.get("reply", "")
            if reply and len(reply) > 20:
                return reply.strip()
        return None
    except Exception:
        return None


def _template_research(question: str, diary_snippet: str) -> str:
    """Fallback research when brain is offline — pattern-based summary."""
    # Count keyword frequency in diary for basic pattern analysis
    words = question.lower().split()
    relevant_lines = []
    for line in diary_snippet.split("\n"):
        if any(w in line.lower() for w in words if len(w) > 3):
            relevant_lines.append(line.strip()[:120])

    if relevant_lines:
        findings = (
            f"I looked through my diary for patterns related to '{question[:80]}'. "
            f"Found {len(relevant_lines)} relevant entries. "
            f"Most recent: \"{relevant_lines[-1]}\". "
            f"[confidence: low]"
        )
    else:
        findings = (
            f"I don't have enough data to investigate '{question[:80]}' properly. "
            f"The diary shows no clear pattern related to this. I should keep watching. "
            f"[confidence: low]"
        )
    return findings


def _store_findings(result: ResearchResult) -> str:
    """Store research findings as a memory entry via the daemon API.

    Returns the memory ref if successful, empty string otherwise.
    """
    try:
        import urllib.request as _ureq

        token_path = VEX_HOME / ".vex_token"
        token = token_path.read_text().strip() if token_path.exists() else ""

        payload = json.dumps({
            "summary": f"Curiosity research: {result.question[:200]}",
            "decisions": [f"Research finding [{result.confidence} confidence]: {result.findings[:500]}"],
            "skills": {},
            "relationships": {},
        }).encode()

        req = _ureq.Request(
            "http://localhost:8520/memory",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with _ureq.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read())
            return resp.get("ref", "")
    except Exception:
        # Write directly to memory file as fallback
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            memory_file = VEX_HOME / "vex_memory" / f"{today}.jsonl"
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            entry = json.dumps({
                "date": today,
                "timestamp": result.timestamp,
                "summary": f"Curiosity research: {result.question[:200]}",
                "decisions": [f"Finding: {result.findings[:300]}"],
                "skills": {},
                "source": "curiosity_research",
                "confidence": result.confidence,
            })
            with open(memory_file, "a") as f:
                f.write(entry + "\n")
            return f"file:{today}"
        except Exception:
            return ""
