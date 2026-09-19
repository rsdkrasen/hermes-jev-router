"""Typed Pydantic outputs for Jev judgments. Questions live ONLY in Field(description=...)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ChunkScore(BaseModel):
    chunk_index: int = Field(description="Zero-based index of the chunk being scored")
    relevance: float = Field(description="How relevant is this chunk to the user goal? 0.0–1.0")
    keep: bool = Field(description="Should this original chunk be preserved in the compacted output?")


class CompactionJudgment(BaseModel):
    scores: List[ChunkScore] = Field(
        description="Relevance judgment per chunk. Prefer keeping diagnostic and goal-critical chunks."
    )


class DuplicateJudgment(BaseModel):
    redundancy: float = Field(
        description="How redundant is this proposed call vs recent identical/near-identical calls? 0.0–1.0"
    )
    relevance: float = Field(
        description="How useful would running this call again be right now? 0.0–1.0"
    )
    is_observational: bool = Field(
        description="Is this a read-only / observational call with no side effects?"
    )
    reason: str = Field(description="One short sentence explaining the judgment")


class GoalClass(BaseModel):
    expects_explanation: bool = Field(
        description="Does the user expect a narrative explanation, not just a factual outcome?"
    )
    goal_kind: Literal[
        "verify", "mutate", "investigate", "explain", "other"
    ] = Field(description="Coarse classification of the user goal")
    short_goal: str = Field(description="Compressed one-line restatement of the user goal")


class RoundControlJudgment(BaseModel):
    goal_satisfied: float = Field(
        description="Is the user goal already satisfied by the tool evidence? 0.0–1.0"
    )
    evidence_sufficient: float = Field(
        description="Is the evidence enough to answer without another main-model call? 0.0–1.0"
    )
    contains_failure: float = Field(
        description="Do the tool results indicate a meaningful failure? 0.0–1.0"
    )
    another_tool_needed: float = Field(
        description="Is another tool call still required before answering? 0.0–1.0"
    )
    requires_main_model: float = Field(
        description="Does answering require generative reasoning from the main LLM? 0.0–1.0"
    )
    outcome: Literal["success", "partial", "failure", "unknown"] = Field(
        description="Overall outcome of this tool round"
    )
    expects_explanation: bool = Field(
        description="Would the user expect a narrative explanation beyond a concise factual answer?"
    )
    evidence_bullets: List[str] = Field(
        description="Short factual bullets taken ONLY from tool evidence (no invention)"
    )
    can_render_deterministically: bool = Field(
        description="Can a concise final answer be rendered from evidence alone without inventing?"
    )


class PlanGateJudgment(BaseModel):
    allow_next_step: bool = Field(description="Should the next pre-specified plan step run now?")
    reason: str = Field(description="One short sentence explaining the gate decision")
    skip_remaining: bool = Field(
        description="Should the rest of the autonomous plan be abandoned?"
    )


class PlanStep(BaseModel):
    tool_name: str
    args: dict = Field(default_factory=dict)
    note: str = ""


class ExecutionPlan(BaseModel):
    steps: List[PlanStep] = Field(default_factory=list)
    current_index: int = 0
    active: bool = False
