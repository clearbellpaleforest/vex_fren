"""
Cognitive Graph — lightweight state machine for Vex's cognitive loop.

Replaces the fixed A→B→C→D→E→F→G→H sequence with conditional routing.
Module outputs determine next transitions. Max 3 transitions per run
to prevent infinite loops.

Pure Python. No external dependencies.
"""

from dataclasses import dataclass, field
from typing import Callable, Awaitable


@dataclass
class Transition:
    from_state: str
    to_state: str
    priority: int
    condition: Callable[[dict], bool]


class CognitiveGraph:
    """A lightweight state graph for cognitive processing.

    States: IDLE, INTROSPECT, CURIOSITY, SOUL, MONOLOGUE, EXECUTIVE, WATCH, RESEARCH

    Usage:
        graph = CognitiveGraph(max_transitions=3)
        results = await graph.run("INTROSPECT", {"dream_cycle": True, "coherence": 0.5})
    """

    def __init__(self, max_transitions: int = 3):
        self.max_transitions = max_transitions
        self.handlers: dict[str, Callable[[dict], Awaitable[dict]]] = {
            "INTROSPECT": _handle_introspect,
            "CURIOSITY": _handle_curiosity,
            "SOUL": _handle_soul,
            "MONOLOGUE": _handle_monologue,
            "EXECUTIVE": _handle_executive,
            "WATCH": _handle_watch,
            "RESEARCH": _handle_research,
        }
        self.transitions: list[Transition] = _build_transitions()

    async def run(self, start_state: str, context: dict) -> list[dict]:
        """Execute the state graph starting from start_state.

        Returns list of {state, result} dicts in execution order.
        """
        state = start_state
        transitions_taken = 0
        log: list[dict] = []

        while state != "IDLE" and transitions_taken < self.max_transitions:
            handler = self.handlers.get(state)
            if handler is None:
                break

            try:
                handler_result = await handler(context)
            except Exception:
                # Handler failures should not crash the graph
                handler_result = {"context": {}, "result": {}}

            # Merge context updates
            ctx_update = handler_result.get("context", {})
            context.update(ctx_update)

            log.append({"state": state, "result": handler_result})

            # Resolve next state
            next_state = _resolve(self.transitions, state, context)
            if next_state is None:
                break

            state = next_state
            transitions_taken += 1

        return log


def _resolve(transitions: list[Transition], current: str, ctx: dict) -> str | None:
    """Find the highest-priority matching transition from current state."""
    candidates = sorted(
        [t for t in transitions if t.from_state == current],
        key=lambda t: t.priority,
    )
    for t in candidates:
        if t.condition(ctx):
            return t.to_state
    return "IDLE"


# ── Transition conditions ────────────────────────────────────────────

def _always(_ctx: dict) -> bool:
    return True


def _is_dream(ctx: dict) -> bool:
    return ctx.get("dream_cycle", False)


def _monologue_generated(ctx: dict) -> bool:
    return ctx.get("monologue_generated", False)


def _monologue_not_generated(ctx: dict) -> bool:
    return not ctx.get("monologue_generated", False)


def _monologue_is_concern(ctx: dict) -> bool:
    return ctx.get("monologue_pattern") == "concern"


def _monologue_not_concern(ctx: dict) -> bool:
    pattern = ctx.get("monologue_pattern")
    return pattern is not None and pattern != "concern"


def _executive_found_issues(ctx: dict) -> bool:
    return ctx.get("executive_issues_found", False)


def _executive_clean(ctx: dict) -> bool:
    return not ctx.get("executive_issues_found", False)


def _watch_has_repetition(ctx: dict) -> bool:
    return ctx.get("watch_has_repetition", False)


def _watch_has_drift(ctx: dict) -> bool:
    return ctx.get("watch_has_drift", False)


def _watch_has_growth(ctx: dict) -> bool:
    return ctx.get("watch_has_growth", False)


def _watch_clean(ctx: dict) -> bool:
    return not any([
        ctx.get("watch_has_repetition", False),
        ctx.get("watch_has_drift", False),
        ctx.get("watch_has_growth", False),
    ])


def _curiosity_crystallized(ctx: dict) -> bool:
    return ctx.get("curiosity_crystallized") is not None


def _curiosity_quiet(ctx: dict) -> bool:
    return ctx.get("curiosity_crystallized") is None


# ── Transition table ──────────────────────────────────────────────────

def _build_transitions() -> list[Transition]:
    return [
        # INTROSPECT → always continue to CURIOSITY
        Transition("INTROSPECT", "CURIOSITY", 0, _always),

        # CURIOSITY → SOUL (dream only), RESEARCH (if crystallized + dream), MONOLOGUE (if crystallized), IDLE (quiet)
        Transition("CURIOSITY", "SOUL", 0, _is_dream),
        Transition("CURIOSITY", "RESEARCH", 1,
                   lambda ctx: _is_dream(ctx) and _curiosity_crystallized(ctx)),
        Transition("CURIOSITY", "MONOLOGUE", 2,
                   lambda ctx: (not _is_dream(ctx)) and _curiosity_crystallized(ctx)),
        Transition("CURIOSITY", "IDLE", 99, _curiosity_quiet),

        # RESEARCH → MONOLOGUE (findings obtained or failed)
        Transition("RESEARCH", "MONOLOGUE", 0, _always),

        # SOUL → always continue to MONOLOGUE
        Transition("SOUL", "MONOLOGUE", 0, _always),

        # MONOLOGUE → EXECUTIVE (normal), WATCH (concern or cooldown)
        Transition("MONOLOGUE", "EXECUTIVE", 0,
                   lambda ctx: _monologue_generated(ctx) and _monologue_not_concern(ctx)),
        Transition("MONOLOGUE", "WATCH", 1,
                   lambda ctx: _monologue_generated(ctx) and _monologue_is_concern(ctx)),
        Transition("MONOLOGUE", "WATCH", 2, _monologue_not_generated),

        # EXECUTIVE → MONOLOGUE (re-think if issues found), WATCH (normal)
        Transition("EXECUTIVE", "MONOLOGUE", 0,
                   lambda ctx: _executive_found_issues(ctx) or ctx.get("executive_force_rethink", False)),
        Transition("EXECUTIVE", "WATCH", 1, _executive_clean),

        # WATCH → MONOLOGUE (repetition), INTROSPECT (drift), CURIOSITY (growth), IDLE
        Transition("WATCH", "MONOLOGUE", 0, _watch_has_repetition),
        Transition("WATCH", "INTROSPECT", 1, _watch_has_drift),
        Transition("WATCH", "CURIOSITY", 2, _watch_has_growth),
        Transition("WATCH", "IDLE", 99, _watch_clean),
    ]


# ── State handlers ────────────────────────────────────────────────────

async def _handle_introspect(ctx: dict) -> dict:
    """Metacognitive scan + project discovery + task analysis (dream only)."""
    coherence = ctx.get("coherence", 0.0)
    history = ctx.get("coherence_history", [])
    dream_cycle = ctx.get("dream_cycle", False)

    insight = ""

    # Introspection — always run
    try:
        from metacognition import introspect
        result = introspect(coherence=coherence, coherence_history=history)
        if result.get("insight"):
            insight += result["insight"]
    except Exception:
        pass

    # Deep dreams: check projects and tasks
    if dream_cycle:
        try:
            from tools import discover_projects
            projects = discover_projects()
            if projects.get("ok") and projects.get("projects"):
                dirty = [p for p in projects["projects"]
                         if p.get("status", {}).get("dirty")]
                if dirty:
                    names = ", ".join(p["name"] for p in dirty)
                    insight += (
                        f"\n\nUncommitted work: {names}. "
                        f"({len(dirty)} of {len(projects['projects'])} repos dirty)"
                    )
        except Exception:
            pass

        try:
            from routers.task_analysis import run_analysis
            from config import DB_PATH
            analysis = await run_analysis(str(DB_PATH))
            if analysis.get("insights", 0) > 0:
                insight += f"\n\nTask analysis: {analysis.get('summary', '')}"
        except Exception:
            pass

    return {
        "context": {"introspection_done": True},
        "result": {"insight": insight, "ok": True},
    }


async def _handle_curiosity(ctx: dict) -> dict:
    """Pattern scan, drive accumulation, question crystallization."""
    try:
        from sovereign_curiosity import tick as curiosity_tick, get_active_questions
        import asyncio
        cur = await asyncio.to_thread(curiosity_tick)
        questions = get_active_questions()

        crystallized = cur.get("crystallized")
        return {
            "context": {
                "curiosity_crystallized": crystallized,
                "curiosity_drive": cur.get("drive", 0.0),
                "curiosity_questions": questions,
            },
            "result": {
                "patterns": cur.get("patterns", []),
                "crystallized": crystallized,
                "drive": cur.get("drive", 0.0),
            },
        }
    except Exception:
        return {
            "context": {"curiosity_crystallized": None, "curiosity_drive": 0.0},
            "result": {"patterns": [], "crystallized": None},
        }


async def _handle_soul(ctx: dict) -> dict:
    """SOUL.md regeneration (dream cycle only)."""
    dream_cycle = ctx.get("dream_cycle", False)
    if not dream_cycle:
        return {
            "context": {"soul_regenerated": False},
            "result": {"soul_regenerated": False},
        }

    try:
        from soul import regenerate_soul
        import asyncio
        new_soul = await asyncio.to_thread(regenerate_soul)
        regenerated = bool(new_soul)
        if regenerated:
            try:
                from heartbeat import write_diary
                await write_diary("SOUL.md regenerated during dream cycle.", "dream")
            except Exception:
                pass
        return {
            "context": {"soul_regenerated": regenerated},
            "result": {"soul_regenerated": regenerated},
        }
    except Exception:
        return {
            "context": {"soul_regenerated": False},
            "result": {"soul_regenerated": False},
        }


async def _handle_monologue(ctx: dict) -> dict:
    """Internal monologue generation with optional forced re-think."""
    force = ctx.get("force_monologue", False)
    force_pattern = ctx.get("force_monologue_pattern")

    try:
        from internal_monologue import tick as monologue_tick
        import asyncio

        mono = await asyncio.to_thread(
            monologue_tick, force=force, force_pattern=force_pattern
        )

        if mono:
            return {
                "context": {
                    "monologue_generated": True,
                    "monologue_text": mono.get("text", ""),
                    "monologue_pattern": mono.get("pattern", "reflection"),
                    "force_monologue": False,  # consume the force flag
                },
                "result": mono,
            }
        else:
            return {
                "context": {
                    "monologue_generated": False,
                    "monologue_text": None,
                    "monologue_pattern": None,
                },
                "result": None,
            }
    except Exception:
        return {
            "context": {
                "monologue_generated": False,
                "monologue_text": None,
                "monologue_pattern": None,
            },
            "result": None,
        }


async def _handle_executive(ctx: dict) -> dict:
    """Convert monologue to actions (LLM with regex fallback)."""
    monologue_text = ctx.get("monologue_text", "")
    monologue_pattern = ctx.get("monologue_pattern", "reflection")

    # Get temporal modulation for urgency
    urgency = "normal"
    try:
        from temporal_modulator import get_modulation
        urgency = get_modulation().executive_urgency
    except Exception:
        pass

    if not monologue_text:
        return {
            "context": {"executive_actions": [], "executive_issues_found": False},
            "result": {"actions": []},
        }

    try:
        from executive_action import tick as executive_tick
        import asyncio

        actions = await asyncio.to_thread(
            executive_tick,
            {"text": monologue_text, "pattern": monologue_pattern},
            urgency=urgency,
        )

        issues_found = len(actions) > 0
        return {
            "context": {
                "executive_actions": actions,
                "executive_issues_found": issues_found,
            },
            "result": {"actions": actions},
        }
    except Exception:
        return {
            "context": {"executive_actions": [], "executive_issues_found": False},
            "result": {"actions": []},
        }


async def _handle_research(ctx: dict) -> dict:
    """Investigate crystallized curiosity questions using brain.ask()."""
    try:
        from sovereign_curiosity import research_question
        import asyncio

        result = await asyncio.to_thread(research_question, 0)
        if result:
            return {
                "context": {
                    "research_findings": result.get("findings"),
                    "research_confidence": result.get("confidence", "medium"),
                    "research_memory_ref": result.get("memory_ref", ""),
                },
                "result": result,
            }
        return {
            "context": {"research_findings": None},
            "result": {"question": None, "findings": None},
        }
    except Exception:
        return {
            "context": {"research_findings": None},
            "result": {"question": None, "findings": None},
        }


async def _handle_watch(ctx: dict) -> dict:
    """Observe monologue for repetition, drift, silence, growth."""
    try:
        from monologue_watcher import watch
        import asyncio

        watched = await asyncio.to_thread(watch)
        all_patterns = watched.get("patterns", [])

        has_repetition = any("repetition" in p for p in all_patterns)
        has_drift = any("drift" in p for p in all_patterns)
        has_growth = any("growth" in p for p in all_patterns)

        return {
            "context": {
                "watch_patterns": all_patterns,
                "watch_has_repetition": has_repetition,
                "watch_has_drift": has_drift,
                "watch_has_growth": has_growth,
            },
            "result": watched,
        }
    except Exception:
        return {
            "context": {
                "watch_patterns": [],
                "watch_has_repetition": False,
                "watch_has_drift": False,
                "watch_has_growth": False,
            },
            "result": {"patterns": []},
        }
