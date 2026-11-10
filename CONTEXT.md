# starbase

A live mission-control dashboard for upcoming rocket launches: it counts down to the next launch, flags holds, links the webcast, and answers questions about everything it has stored.

## Language

### The board

**Board**:
The single view a visitor sees: the hero plus the strip.
_Avoid_: dashboard (that is the whole site), homepage

**Hero**:
The launch currently featured by the board: an In Flight launch when one exists, otherwise the next upcoming launch.

**Strip**:
The row of upcoming launch cards shown alongside the hero.

**Lane**:
The partition a stored launch belongs to: upcoming or past.
_Avoid_: category, bucket

**NET**:
"No earlier than" — the planned launch time. Coarse NETs (day precision or fuzzier) render as a date label instead of a countdown.

**Hold**:
A launch paused before flight, with a reason. Replaces the countdown.

**Stale**:
Board data older than 30 minutes since the last successful ingest. Drives the DATA STALE chip and the health 503.

### CAPCOM

**CAPCOM**:
The mission-control conversationalist: the chat persona that answers visitor questions from stored state.
_Avoid_: chatbot, copilot, assistant, "Ask starbase"

**Answer Policy**:
Two-tier contract: launch facts (dates, statuses, schedules, vehicles, pads, missions, holds, webcasts) come only from stored state; general space knowledge is answered with a disclosure that it is not live data.

**Knowledge Boundary**:
CAPCOM knows the starbase table and nothing else. Launch Library 2 is never a CAPCOM tool (see ADR 0001).

**Daily Quota**:
The shared, per-UTC-day budget of CAPCOM questions. When exhausted, CAPCOM goes quiet for everyone until midnight UTC; the rest of the site is unaffected.

### For agents

**Agent API**:
The public read API exposed as agent-callable tools through an AgentCore Gateway — the same four routes, the same read path, a second door.
_Avoid_: MCP endpoint (it is MCP-compatible, but that is a mechanism, not the name)
