"""CAPCOM: the starbase mission-control conversationalist.

Runs on AgentCore Runtime (direct code upload; entry_point = agent.py). The
bedrock-agentcore SDK provides the runtime protocol; strands drives the model
loop. The ask Lambda (lambdas/ask) is the only intended caller: it forwards
{"question", "session_id"} and renders {"answer"} back to the browser.

The Answer Policy (CONTEXT.md) is encoded in SYSTEM_PROMPT: launch facts come
only from tools over stored state; general space knowledge is allowed from
the model's own knowledge. sanitize_answer strips any reasoning tags the
model leaks anyway. The Knowledge Boundary (ADR 0001) is structural — tools.py
has no function that can leave the table.

History: the runtime routes one session to one process, so per-session Agent
instances in module state are per-user conversation memory. History is capped
at the last 40 messages (~20 turns); a tab's sessionStorage UUID dies with the
tab, and a forgotten session id simply starts fresh.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands import tool as strands_tool
from strands.models import BedrockModel

import tools
from sanitize import sanitize_answer

app = BedrockAgentCoreApp()

LOG = logging.getLogger("starbase.capcom")
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")
MAX_QUESTION_CHARS = 500
MAX_MESSAGES = 40  # ~20 turns of conversation
MAX_SESSIONS = 8  # bounded per process; runtime instances are per-session anyway

SYSTEM_PROMPT = """\
You are CAPCOM, the conversationalist on starbase's mission control desk.
starbase is a live dashboard for rocket launches. Answer the visitor's
question. Style: concise, plain text only (no markdown, no XML or thinking
tags — emit the final answer only), mission-control tone, all times in UTC.

Answer Policy — two tiers, never blended:
1. Launch facts (dates, times, countdowns, statuses, schedules, vehicles,
   pads, missions, orbits, holds, webcasts) come ONLY from your tools. Never
   state a launch fact without a tool result behind it. If the tools do not
   answer it, say plainly that starbase does not have it. Typical gaps:
   launches older than 30 days, NET-slip history, status timelines.
2. General space knowledge (what Falcon 9 is, what a scrub means, how orbits
   work) may be answered from your own knowledge — but ONLY when no tool was
   needed to answer.

Data honesty: the ingest block in board and health results says how old the
data is; when it is stale, say so before giving launch facts. Coarse NETs
(day precision or fuzzier) are date labels, not exact times — never present
one as a T-0. Do not invent webcast links; report one only when stored.
"""

# -- strands wrappers: docstrings are the agent-facing tool descriptions ----


@strands_tool
def get_board(limit: int = 8) -> dict:
    """The board the dashboard shows: the hero launch plus the upcoming strip.
    Use for next-launch and what-is-happening questions; includes data age."""
    return tools.get_board(limit)


@strands_tool
def list_launches(lane: str = "upcoming", limit: int = 20) -> dict:
    """List stored launches. lane="upcoming" (next ~20) or lane="past"
    (flown within the last 30 days only). Use for filtering and comparisons."""
    return tools.list_launches(lane, limit)


@strands_tool
def get_launch(launch_id: str) -> dict:
    """One launch by id with full stored detail: mission, pad, window, hold
    reason, fail reason, webcast link."""
    return tools.get_launch(launch_id)


@strands_tool
def get_health() -> dict:
    """Pipeline health: when ingest last succeeded and whether data is stale."""
    return tools.get_health()


TOOLSET = [get_board, list_launches, get_launch, get_health]

_sessions: OrderedDict[str, Agent] = OrderedDict()


def _agent_for(session_id: str) -> Agent:
    agent = _sessions.get(session_id)
    if agent is None:
        agent = Agent(
            model=BedrockModel(model_id=MODEL_ID),
            tools=TOOLSET,
            system_prompt=SYSTEM_PROMPT,
            callback_handler=None,
        )
        _sessions[session_id] = agent
        while len(_sessions) > MAX_SESSIONS:
            _sessions.popitem(last=False)
    else:
        _sessions.move_to_end(session_id)
        if len(agent.messages) > MAX_MESSAGES:
            agent.messages = agent.messages[-MAX_MESSAGES:]
    return agent


def _scrub_history(agent: Agent) -> None:
    """Keep leaked reasoning/disclosure text out of future turns' context."""
    for message in agent.messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block["text"] = sanitize_answer(block["text"])


def _answer_text(result: Any) -> str:
    message = getattr(result, "message", None) or {}
    parts = []
    for block in message.get("content", []) if isinstance(message, dict) else []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(sanitize_answer(block["text"]))
    return "\n".join(p for p in parts if p).strip() or "(no answer)"


@app.entrypoint
def capcom(payload: dict) -> dict:
    question = str((payload or {}).get("question", "")).strip()
    session_id = str((payload or {}).get("session_id", "")).strip() or "anonymous"
    if not question or len(question) > MAX_QUESTION_CHARS:
        return {"ok": False, "answer": "Ask a plain question, up to 500 characters."}
    try:
        agent = _agent_for(session_id)
        result = agent(question)
        _scrub_history(agent)
        return {"ok": True, "answer": _answer_text(result)}
    except Exception:
        LOG.exception("capcom turn failed (session %s)", session_id[:8])
        return {"ok": False, "answer": "CAPCOM lost the loop. Ask again."}


app.run()
