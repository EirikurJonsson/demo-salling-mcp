# salling-mcp

> **⚠️ Unofficial hobby project.** This is **not** made, endorsed, supported or affiliated
> with Salling Group (Netto, føtex, Bilka, Salling) in any way. I built it for fun. It only
> uses Salling Group's public developer APIs, with my own free API key. "Salling Group",
> "Netto", "føtex" and "Bilka" are their trademarks.
>
> Use it at your own risk: prices, stores and opening hours may be wrong or out of date.
> Always check with the store. Please don't contact Salling Group about this project.

A hobby [MCP](https://modelcontextprotocol.io) server that lets an AI assistant talk about
recipes, price the ingredients with the [Salling Group APIs](https://developer.sallinggroup.com),
and point you to nearby Netto, føtex and Bilka stores (Denmark).

## What it can do

| Tool | What it does |
| --- | --- |
| `price_ingredients` | Price a list of ingredients. Gives options within a price range above the cheapest (default 20%), not only the cheapest. Compares price per kg/l/stk when the pack size is in the product title. Give an amount (e.g. `1 kg`) and it counts how many packs to buy. With a postal code, also suggests the nearest Bilka and lists matching food-waste deals nearby. |
| `search_products` | Search products by a Danish term (e.g. `mælk`). |
| `get_similar_products` / `get_frequently_bought_together` | Alternatives and related products. |
| `set_postal_code` | Remember your postal code (postnummer) for the session. |
| `search_stores` | Nearest stores to your postal code, with approximate distance, today's hours, whether they are open now, and a map link. |
| `show_stores_map` | A small map image of the nearest stores, as numbered dots. |
| `find_food_waste` / `get_store_food_waste` | Discounted near-expiry items in nearby stores. |
| `get_store_details` | Address, opening hours for the coming week, open now, and facilities for a store. |

It also has two prompts that clients can offer as ready-made commands:
`recipe_shopping_list` (recipe in, priced shopping list and store out) and
`cook_from_food_waste` (meal ideas from discounted items nearby).

**About prices:** product prices come from Bilka ToGo online, so they are a reference, not
the exact price in a given store. Only food-waste prices are real per-store prices.

**About location:** the server only asks for a postal code, never an address. Over stdio
(one user per server process) it is kept in memory until the server stops, and never written
to disk. If the server is run any other way, for example shared over HTTP, it is not kept at
all, so one user's postal code can never be used for another. Distances are measured from
the middle of the postal code area, so they are approximate.

## Setup

You need [uv](https://docs.astral.sh/uv/) and a free API key from the
[Salling Group developer portal](https://developer.sallinggroup.com).

```sh
git clone <this repo>
cd salling-group-mcp
uv sync
cp .env.example .env   # then put your key in .env
```

`.env` is git-ignored. Do not commit your key.

## Use it with an MCP client

The server talks over stdio:

```sh
uv run salling-mcp
```

For OpenCode, copy `opencode.example.json` to `opencode.json` and change the path to where
you cloned the repo. Other MCP clients use the same command:
`uv run --directory /path/to/salling-group-mcp salling-mcp`.

## Development

```sh
uv run pytest
uv run ruff check src tests
```

Tests mock all HTTP calls and never call the live API. CI runs both on every push and pull
request.

## Credits

Data from the public Salling Group APIs. Map tiles ©
[OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.

This is an unofficial fun project with no connection to Salling Group. If something is
broken, open an issue here, not with them.
