---
name: salling-group-api
description: Guide and instructions for integrating with the Salling Group REST API (Anti Food Waste, Product Suggestions, Stores, Authentication, and MCP Server implementation details). Use when building integrations, tools, or FastMCP servers for Salling Group.
---

# Salling Group API & MCP Integration Guide

This skill provides full reference documentation and guidance for integrating with Salling Group REST APIs and building Model Context Protocol (MCP) servers or client workflows against them.

## Overview & Authentication

Salling Group APIs use Bearer Token authentication.

- **Base URL:** `https://api.sallinggroup.com`
- **Header:** `Authorization: Bearer <YOUR_API_KEY>`

### Setup & Key Management

1. **API Catalog:** Add desired APIs to "My API Collection" in the developer portal.
2. **Application Creation:**
   - Register an application under "Backend / Integration App".
   - Define exact required operation scopes.
3. **API Key Generation:**
   - Generate key under application settings.
   - Select corresponding environment (v1, v2, etc.).
   - Secure key immediately (shown once).

## 1. Anti Food Waste API

Serves heavily discounted food items about to expire across Denmark stores (føtex, Netto, Basalt, Bilka).

### Endpoints

#### A. Nearby Food Waste Clearances

- **Endpoint:** `GET /v1/food-waste/`
- **Headers:** `Authorization: Bearer <token>`
- **Query Parameters:**
  - `zip`: Filter by postal code (e.g. `8000`). Returns up to 20 stores max across any zip.
  - `geo`: Coordinates as `lat,lng` (e.g. `56.154459,10.206777`).
  - `radius`: Proximity radius in kilometers.

#### B. Single Store Food Waste

- **Endpoint:** `GET /v1/food-waste/{store_id}`
- **Headers:** `Authorization: Bearer <token>`

### Key Data Structures

- `clearances[]`: Array of discounted products.
  - `offer`: `currency`, `discount`, `ean`, `startTime`, `endTime`, `lastUpdate`, `originalPrice`, `newPrice`, `percentDiscount`, `stock`, `stockUnit`.
  - `product`: `description`, `ean`, `image`.
- `store`: Detailed store object (`id`, `name`, `brand`, `address`, `coordinates`, `hours`).

> Note: Stock count updates daily/near-realtime but serves primarily as an indicator.

## 2. Product Suggestions API

Powered by machine learning models to recommend items available on Bilka ToGo.

### Endpoints

#### A. Relevant Products Search

- **Endpoint:** `GET /v1/product-suggestions/relevant-products`
- **Query Parameters:** `query` (URL-encoded search term, e.g. `m%C3%A6lk` for milk)

#### B. Similar Products

- **Endpoint:** `GET /v1/product-suggestions/similar-products`
- **Query Parameters:** `productId` (e.g. `31802`)

#### C. Frequently Bought Together

- **Endpoint:** `GET /v1/product-suggestions/frequently-bought-together`
- **Query Parameters:** `productId` (e.g. `31802`)

### Output Structure

Returns lists/arrays of product objects containing: `id`, `prod_id`, `title`, `description`, `price`, `link`, `img`.

## 3. Stores API

Provides metadata and opening hours for ~1,500 physical stores across Denmark, Poland, and Germany (brands: Basalt, Bilka, BR, Carl's Jr, føtex, Netto, Salling, Starbucks).

### Endpoints

#### A. List Stores / Filter Stores

- **Endpoint:** `GET /v2/stores`
- **Query Parameters:**
  - Proximity: `geo=lat,lng`, `radius` (km, default 10)
  - Address filters: `zip`, `city`, `country` (`dk`, `de`, `pl`), `street`
  - Brand filter: `brand` (`br`, `bilka`, `carlsjr`, `foetex`, `netto`, `salling`, `starbucks`)
  - Features: `hourType` (e.g. `store`, `bakery`), `fields` (e.g. `name,address`)
  - Pagination: `per_page`
  - Attribute Boolean Filters: `scanAndGo`, `holidayOpen`, `parking`, `clickAndCollect`, `wifi`, `wc`, `babyChanging`, etc.

#### B. Get Store by ID

- **Endpoint:** `GET /v2/stores/{store_id}`

## Designing an MCP Server for Salling Group API

When building a Model Context Protocol (MCP) server in Python (e.g., using FastMCP or `mcp`), expose these modular tools:

### Tool Specifications

- `get_food_waste(zip_code: str = None, geo: str = None, radius: float = None)`
  - Calls `/v1/food-waste/` using zip or geo.
- `get_store_food_waste(store_id: str)`
  - Calls `/v1/food-waste/{store_id}`.
- `search_products(query: str)`
  - Calls `/v1/product-suggestions/relevant-products?query={query}`.
- `get_similar_products(product_id: str)`
  - Calls `/v1/product-suggestions/similar-products?productId={product_id}`.
- `get_frequently_bought_together(product_id: str)`
  - Calls `/v1/product-suggestions/frequently-bought-together?productId={product_id}`.
- `search_stores(brand: str = None, zip_code: str = None, city: str = None, country: str = "dk", geo: str = None, radius: float = None)`
  - Calls `/v2/stores` with dynamic parameter filters.
- `get_store_details(store_id: str)`
  - Calls `/v2/stores/{store_id}`.

### Standard Execution Pattern (FastMCP Python)

```python
import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("salling-group")

SALLING_API_KEY = os.getenv("SALLING_API_KEY")
BASE_URL = "https://api.sallinggroup.com"

def get_headers():
    return {"Authorization": f"Bearer {SALLING_API_KEY}"}

@mcp.tool()
async def get_food_waste_by_zip(zip_code: str) -> dict:
    """Fetch food waste items offered at stores near a specific zip code in Denmark."""
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{BASE_URL}/v1/food-waste/?zip={zip_code}",
            headers=get_headers()
        )
        res.raise_for_status()
        return res.json()
```
