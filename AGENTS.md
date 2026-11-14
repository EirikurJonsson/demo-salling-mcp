# AGENTS.md

## Project

An MCP server (Python, FastMCP) that lets an agent chat about recipes, price ingredients
using the Salling Group APIs, and point the user to nearby stores.

Two main flows:
1. **Chat:** the user discusses recipes; the agent looks up prices and nearby stores on demand.
2. **Recipe in, shopping list out:** the user gives a recipe; the agent extracts ingredients,
   prices each one, and returns a shopping list with a total and a suggested store.

## Domain rules

- **Price tolerance:** do not always pick the cheapest product. Return options within a
  configurable range above the cheapest (default 20%) so quality is not sacrificed.
- **Price sources are limited:**
  - Product Suggestions = Bilka ToGo online prices (a reference, not per-store prices).
  - Anti Food Waste = real per-store prices, but only discounted near-expiry items.
  - Stores = location, hours, features. No prices.
  - Never claim a price is exact for a specific physical store unless it came from Anti Food Waste.
- **Ingredient extraction** is done by the agent/LLM, not by server code. Tools take plain
  ingredient names (Danish search terms work best, e.g. "mælk", "hakket oksekød").

## Stack

- Python 3.11+, `mcp` 2.x (`MCPServer`, formerly FastMCP: `from mcp.server.mcpserver import MCPServer`),
  `httpx` (async), `python-dotenv`.
- Package/run with `uv`.
- Tests: `pytest` + `pytest-asyncio`; mock HTTP with `respx`. Never hit the live API in tests.

## Layout

```
src/salling_mcp/
  server.py     # MCPServer app, tool and prompt definitions (thin)
  client.py     # Salling Group HTTP client (all API calls, retries, cache, pacing, daily budget)
  cache.py      # SQLite store: cached responses, call log and block state survive restarts
  pricing.py    # price tolerance, unit prices, packs, totals, opening hours (pure, unit tested)
  models.py     # TypedDict output schemas for the tools
  mapimage.py   # static PNG map of nearby stores (OSM tiles, small numbered dots)
tests/          # test_protocol.py goes through a real MCP client
skills/salling-group-api/SKILL.md      # API reference — read before touching client.py
skills/salling-group-api/REFERENCE.md  # official-portal digest: quotas, spike protection, schemas
```

## API limits (read REFERENCE.md §1 and §4)

- Food Waste and Product Suggestions allow **100 calls/day** and have per-IP spike protection.
  `client.py` sends their calls one at a time, spaced out, under a daily budget of 90, and
  caches product results for 24 h on disk (`SALLING_CACHE_PATH`, `off` = memory only).
- A long `Retry-After` on a 429 means quota used up or IP quarantined: stop calling until it
  passes. The block is stored on disk so a restart does not forget it.
- `price_ingredients` takes at most 10 ingredients and has a per-call search budget.
- Never run live calls in a loop while developing; tests use `respx` only.

## Conventions

- Tools stay thin: validate input, call `client.py`, shape output via `pricing.py`.
- One shared `httpx.AsyncClient` with a timeout; call `raise_for_status()` and turn HTTP
  errors into clear tool error messages.
- Type hints and docstrings on every tool. The docstring is what the agent sees, so make it
  say when to use the tool.
- Return compact, trimmed JSON (only fields the agent needs), not raw API responses.
- Raise `ToolError` (or `SallingAPIError`, a subclass) for anything the agent should read.
  MCPServer replaces the text of any other exception with "Error executing tool X".
- Output types live in `models.py` and must use `typing_extensions.TypedDict` with
  `extra="allow"`: on Python 3.11 `typing.TypedDict` silently removes the schema, and
  without `extra="allow"` undeclared fields are dropped from structured output.
- The postal code is remembered only when running over stdio (`main()`), never over HTTP.

## Secrets

- API key lives in `.env` as `SALLING_API_KEY`. `.env` is git-ignored; keep `.env.example` current.
- Never log, print, or return the key.

## Commands

```
uv sync                                  # install
uv run salling-mcp                       # run the MCP server (stdio)
uv run pytest                            # tests
uv run ruff check src tests              # lint
```
