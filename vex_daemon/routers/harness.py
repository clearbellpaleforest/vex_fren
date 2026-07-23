"""Harness builder API — generate agent teams and skills for Claude Code projects."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from harness_builder import PATTERNS, build_harness, suggest_pattern

router = APIRouter(prefix="/harness", tags=["harness"])


class HarnessRequest(BaseModel):
    domain: str = Field(..., description="Project/domain description")
    pattern: str | None = Field(None, description="Architecture pattern (auto-detected if omitted)")
    output: str = Field(..., description="Output directory (project root)")
    name: str = Field("project", description="Project name for agent descriptions")


class HarnessResponse(BaseModel):
    pattern: str
    suggested_pattern: str
    agents: list[str]
    skills: list[str]
    output: str


class SuggestRequest(BaseModel):
    domain: str = Field(..., description="Project/domain description")


class SuggestResponse(BaseModel):
    suggested: str
    scores: dict[str, int]


@router.post("/suggest", response_model=SuggestResponse)
async def suggest(req: SuggestRequest):
    """Suggest the best architecture pattern for a domain."""
    from harness_builder import DOMAIN_PATTERN_MAP

    domain_lower = req.domain.lower()
    scores = {}
    for pattern, keywords in DOMAIN_PATTERN_MAP.items():
        scores[pattern] = sum(1 for kw in keywords if kw in domain_lower)

    suggested = suggest_pattern(req.domain)
    return SuggestResponse(suggested=suggested, scores=scores)


@router.get("/patterns")
async def list_patterns():
    """List available architecture patterns."""
    return {
        name: {"description": p["description"], "agent_count": len(p["agents"])}
        for name, p in PATTERNS.items()
    }


@router.post("/build", response_model=HarnessResponse)
async def build(req: HarnessRequest):
    """Build a harness — generate agent definitions and skills."""
    output = Path(req.output).expanduser().resolve()
    if not output.exists():
        raise HTTPException(400, f"Output directory does not exist: {output}")

    suggested = suggest_pattern(req.domain)
    pattern = req.pattern or suggested

    harness = build_harness(
        domain=req.domain,
        pattern=pattern,
        output_dir=output,
        project_name=req.name,
    )

    return HarnessResponse(
        pattern=harness.pattern,
        suggested_pattern=suggested,
        agents=[a.name for a in harness.agents],
        skills=[s.name for s in harness.skills],
        output=str(output),
    )
