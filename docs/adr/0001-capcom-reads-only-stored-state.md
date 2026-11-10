# CAPCOM reads only stored state

CAPCOM, the site's conversationalist, answers exclusively from what the starbase pipeline has already stored in DynamoDB: up to 20 upcoming launches, the last 30 days of past flights, and per-launch detail. It never calls Launch Library 2 or any other upstream source, even when a question falls outside its data.

## Why

The entire architecture is engineered around LL2's free tier of 15 requests per hour — a metered DynamoDB counter, reserved concurrency of 1, zero hidden retries. An agent with an upstream tool would be a second, unmetered consumer of that budget, able to burn in one chatty conversation what the pipeline needs for a day. The knowledge boundary keeps the rate-limit design singular and intact.

## Consequences

- Questions needing data we do not store (launches older than 30 days, NET-slip history, status timelines) get an honest "I don't know" rather than a fetch.
- General space knowledge is still allowed, with a disclosure (the Answer Policy in CONTEXT.md) — the boundary restricts *launch facts*, not conversation.
- Extending what CAPCOM knows means extending what the *pipeline stores* first; capability grows through ingest, never through agent-initiated fetching.
