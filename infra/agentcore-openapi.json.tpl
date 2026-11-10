{
  "openapi": "3.0.3",
  "info": {
    "title": "starbase",
    "version": "1.0.0",
    "description": "Live rocket-launch mission data: upcoming launches (up to 20 stored), flights from the last 30 days, per-launch detail, and pipeline health. All times UTC, ISO-8601 Zulu. Launch data is refreshed from Launch Library 2 every ~10 minutes."
  },
  "servers": [{ "url": "${api_endpoint}" }],
  "paths": {
    "/api/v1/board": {
      "get": {
        "summary": "The board the dashboard shows",
        "description": "The featured (hero) launch — an In Flight launch when one exists, otherwise the next upcoming launch — plus the strip of upcoming launches behind it. The ingest block reports how fresh the data is; stale means older than 30 minutes.",
        "operationId": "get_board",
        "parameters": [
          {
            "name": "limit",
            "in": "query",
            "description": "Maximum strip launches to return (1-20, default 8)",
            "required": false,
            "schema": { "type": "integer", "minimum": 1, "maximum": 20, "default": 8 }
          }
        ],
        "responses": {
          "200": {
            "description": "Board: hero launch, strip, ingest freshness",
            "content": { "application/json": { "schema": { "type": "object" } } }
          }
        }
      }
    },
    "/api/v1/launches": {
      "get": {
        "summary": "List launches by lane",
        "description": "Upcoming launches sorted by launch time (at most 20 are stored), or past flights sorted most-recent-first (only the last 30 days are retained). Use lane=upcoming for schedules and lane=past for recent history.",
        "operationId": "list_launches",
        "parameters": [
          {
            "name": "lane",
            "in": "query",
            "description": "Which lane to list",
            "required": false,
            "schema": { "type": "string", "enum": ["upcoming", "past"], "default": "upcoming" }
          },
          {
            "name": "limit",
            "in": "query",
            "description": "Maximum launches to return (1-50, default 20)",
            "required": false,
            "schema": { "type": "integer", "minimum": 1, "maximum": 50, "default": 20 }
          }
        ],
        "responses": {
          "200": {
            "description": "Launch list",
            "content": { "application/json": { "schema": { "type": "object" } } }
          }
        }
      }
    },
    "/api/v1/launches/{id}": {
      "get": {
        "summary": "One launch by id",
        "description": "Full stored detail for a single launch: NET and its precision, window, status, provider, vehicle, pad, mission, orbit, hold/fail reason, webcast link.",
        "operationId": "get_launch",
        "parameters": [
          {
            "name": "id",
            "in": "path",
            "description": "Launch id (alphanumeric with dashes, as returned by get_board/list_launches)",
            "required": true,
            "schema": { "type": "string", "maxLength": 64 }
          }
        ],
        "responses": {
          "200": {
            "description": "Launch detail",
            "content": { "application/json": { "schema": { "type": "object" } } }
          },
          "404": { "description": "No such launch" }
        }
      }
    },
    "/api/v1/health": {
      "get": {
        "summary": "Pipeline health and staleness",
        "description": "When ingest last ran and last succeeded. Returns 503 with status=degraded when data is stale (> 30 minutes since the last successful refresh). Use to hedge the freshness of other answers.",
        "operationId": "get_health",
        "parameters": [],
        "responses": {
          "200": { "description": "Healthy", "content": { "application/json": { "schema": { "type": "object" } } } },
          "503": { "description": "Degraded: stale data" }
        }
      }
    }
  }
}
