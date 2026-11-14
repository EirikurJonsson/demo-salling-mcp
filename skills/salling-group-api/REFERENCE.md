# Salling Group Developer API — reference for AI agents

Compiled 2026-11-14 from the official developer portal
(https://developer.sallinggroup.dev): the public API reference pages, the
signed-in API Catalog, and each API's OpenAPI specification (OAS).

This is an unofficial digest. When this file and a live response disagree, the
live response wins. Where the portal's guide pages and its OpenAPI specs
disagree, both versions are given and marked **[conflict]** — resolve those
with one real request before relying on either.

Do not invent endpoints, parameters or fields that are not listed here.

---

## 1. Overview

- Base URL: `https://api.sallinggroup.com`
- HTTPS only, TLS 1.2 or newer.
- Every documented operation is a `GET`.
- Support: apisupport@sallinggroup.com. The old portal
  (`developer.sallinggroup.com`) is retired.

| API | Path prefix | Default quota | Key may live in a front end? | Notes |
|-----|-------------|---------------|------------------------------|-------|
| Food Waste | `/v1/food-waste` | 100 / day | yes | spike protection |
| Product Suggestions (Beta) | `/v1/product-suggestions` | 100 / day | yes | spike protection |
| Stores | `/v2/stores` | 100,000 / day | yes | |
| Holidays | `/v1/holidays` | 10,000 / day | yes | ETag support |
| Jobs | `/v1/jobs` | 1,000 / hour | yes | |
| Products EAN | `/v2/products/{ean}` | 100 / day | **no — back end only** | educational, non-commercial use only |
| Recommendations | `/v1/recommendations/most-bought/…` | 100 / day | **no — back end only** | educational, non-commercial use only; undocumented |

Quotas are the portal's stated defaults per API product.

---

## 2. Getting an API key

1. Sign in at https://developer.sallinggroup.dev.
2. **API Catalog** → add the API to "My API Collection". Some are granted
   instantly; others go through approval and ask for a short description of the
   intended use.
3. **Applications** → register an application, type "Frontend / Website App" or
   "Backend / Integration App", and select the operations it will call.
4. On the application page, create an API key: name it, select the exact
   operations, and pick the environment where several exist (e.g. `v1` vs
   `v1-test`). Front-end keys also need the calling origins listed.
5. Store the key immediately. It is displayed once; if lost, create a new one
   or rotate it.

A key only works for the operations in its scope. Outside the scope expect 403.

---

## 3. Authentication

Use **Bearer**. Not every API supports every scheme.

```
Authorization: Bearer <token>
```

```bash
curl -H "Authorization: Bearer $SALLING_TOKEN" https://api.sallinggroup.com/v2/stores
```

Other schemes:

- **API key in URL** — `?apiKey=<token>`. Exists to avoid CORS preflight in
  browsers; the key ends up in every URL. Avoid.
- **JWT — deprecated.** Header `Authorization: JWT <token>`; one token per
  request, valid about 15 minutes; signed with the project secret using
  `HS256`, `HS384` or `HS512`. Claims: `iss` (project issuer token), `sub`
  (request path + query, not URL-encoded), `mth` (HTTP method), `exp` (Unix
  seconds). Do not use for new work.

---

## 4. Cross-cutting behaviour

### Quota

Over quota → **HTTP 429** with `Retry-After` = seconds until the quota resets.
See the table in section 1.

### Spike protection (Food Waste, Product Suggestions)

Enforced per **IP address**. An IP that sends too many requests in a short time
is quarantined and receives no responses until the quarantine ends.

`Retry-After` on a normal response is the number of seconds to wait before the
next request is safe; sending earlier moves the IP toward quarantine (a small
service-specific buffer is tolerated). When quarantined, it says when the
quarantine ends.

### Pagination (Stores, Jobs)

- `page` — 1-based, default 1. `per_page` — default 10.
- A page out of range returns **HTTP 400**.
- `Link` response header holds ready-made URLs with `rel` = `first`, `prev`,
  `next`, `last`. Follow them instead of building URLs.
- `X-Total-Count` response header is the total match count ignoring
  pagination. `totalPages = ceil(X-Total-Count / per_page)`.

### Field selection (Stores, Jobs)

`fields=a,b,c` returns only those top-level fields. Works on list and
single-item endpoints.

### Errors

Standard HTTP status codes. **The JSON error body is not uniform across APIs:**

| API | Error body fields |
|-----|-------------------|
| Stores (and the generic guide) | `errorCode`, `developerMessage`, `userMessage`, `moreInfo` |
| Food Waste | `code`, `developerMessage`, `statusCode`, `moreInfo` |
| Product Suggestions | `errorMessage`, `statusCode`, `moreInfo` |
| Jobs, Products EAN | `errorMessage`, `contactInformation`, `statusCode` |

Branch on the HTTP status, not on body fields.

Common statuses: 400 bad parameters, 401 missing/unknown credentials, 403
credentials lack access, 404 not found, 429 too many requests (quota or IP),
500/502/503/504 upstream problems.

### CORS

Browser calls work only from origins whitelisted on the project/key in the
portal. An origin is protocol + host (+ port), e.g. `http://localhost:3000`.

---

## 5. Food Waste API

Clearance prices on food close to expiry in Danish føtex, Netto, Bilka and
Basalt stores.

Data rules:

- `stock` is indicative, not real-time.
- Only clearances created, or whose stock changed, yesterday or today are
  served.

### 5.1 `GET /v1/food-waste` — nearby stores with clearances

| Param | Type | Notes |
|-------|------|-------|
| `zip` | integer | Danish postal code, e.g. `8660` |
| `geo` | string | `latitude,longitude`, e.g. `56.183,10.105` |
| `radius` | number | km around `geo`; default 10; ignored without `geo` |

At most **20 stores** per request. A wide radius silently truncates the list.

Statuses: 200, 400, 401, 500. Returns an **array** of FoodWaste objects.

### 5.2 `GET /v1/food-waste/{storeId}` — one store

`storeId` (path, required) is the store UUID from the Stores API.

Statuses: 200; 400 if the store is not a Danish føtex, Netto or Bilka or no id
was given; 401; 404 unknown store id; 500. Returns one **FoodWaste object**.

### 5.3 Schema

```jsonc
// FoodWaste
{
  "clearances": [
    {
      "offer": {
        "currency": "DKK",                       // enum: DKK
        "discount": 6,                           // amount off
        "ean": "20002275",                       // EAN of this specific offer (the markdown label)
        "startTime": "2019-10-18T08:41:17.000Z", // ISO 8601
        "endTime": "2019-11-14T23:00:00.000Z",
        "lastUpdate": "2019-10-28T15:34:39.000Z",
        "newPrice": 18,                          // price to pay
        "originalPrice": 22,
        "percentDiscount": 18.18,
        "stock": 88,                             // quantity on clearance
        "stockUnit": "each"                      // enum: each | kg; nullable
      },
      "product": {
        "description": "WASA HUSMAN 520 G",      // text on the in-store price badge
        "ean": "5710405175931",                  // EAN of the product itself
        "image": "https://…",                    // nullable
        "categories": {                          // '>'-separated category path
          "da": "Pizza>Færdigretter>Mejeri & køl>…",
          "en": "Ready To Eat Pizza>Ready To Eat Meals>…"
        }
      }
    }
  ],
  "store": {
    "id": "78c129a0-6273-475f-9640-cec700f11948",   // uuid
    "name": "Netto Århushallen",
    "brand": "netto",
    "address": { "street": "Agerøvej 7", "zip": "8381", "city": "Tilst",
                 "country": "DK", "extra": null },
    "coordinates": [10.104849, 56.182413],          // [longitude, latitude]
    "distance_km": 3.5,                             // only on geo requests
    "type": "Point",
    "hours": [                                      // exactly 2: today and tomorrow
      { "date": "…", "type": "store", "open": "…", "close": "…",
        "closed": false, "customerFlow": [0] }      // open/close nullable
    ]
  }
}
```

Gotchas:

- `offer.ean` ≠ `product.ean`. Use `product.ean` to identify the product and
  to call Products EAN.
- Request `geo` is `lat,lng`; response `coordinates` is `[lng, lat]`.
- `stockUnit` can be `kg`, in which case `stock` is a weight.

---

## 6. Stores API

About 1,700 stores: location, opening hours, attributes and busy-hour
indicators.

### 6.1 `GET /v2/stores` — list

| Param | Type | Notes |
|-------|------|-------|
| `brand` | string | `bilka`, `foetex`, `netto`, `basalt`, `salling`, `br`, `carlsjr`, `starbucks`. Case-insensitive. |
| `country` | string | ISO 3166-1 alpha-2: `DK`, `DE`, `SE`, `PL` |
| `city` | string | exact, case-insensitive match. `københavn` matches nothing; `københavn nv` does. |
| `zip` | string | local postal code |
| `geo` | string | `latitude,longitude`; truncated to 4 decimals |
| `radius` | number | km, up to 2 decimals; ignored without `geo`. Default **[conflict]**: OAS says 5, guide says 10 — always pass it. |
| `hourType` | string | filters each store's `hours` array, not which stores are returned. Values: `store`, `gardencenter`, `bakery`, `bilkatogo`, `foodClickCollect`, `nonfoodClickCollect`, `flowers`, `vareudlevering` (pickup location), `servicecenter`, `bistro`, `quickfood`, `sushi`, `pakketarn` (parcel pickup tower) |
| `sapSiteId` | string | filter by SAP site id |
| `vikingStoreId` | string | filter by Viking store id |
| `fields` | csv | field selection |
| `page`, `per_page` | int | defaults 1 and 10 |
| attribute filters | boolean | see 6.4 |

The guide also lists a `street` filter (exact match); it is absent from the
OAS.

Statuses: 200, 400, 401, 403, 429, 500, 503, 504.

### 6.2 `GET /v2/stores/{id}` — one store

`id` (uuid, required); optional `fields`.

### 6.3 `GET /v2/stores/{id}/schedules` — opening-hour schedules and deviations

Statuses: 200, 404, 500, 503, 504.

```jsonc
{
  "chain": { "deviations": [ … ], "schedules": [ … ] },   // chain-wide
  "store": {                                              // this store
    "deviations": [
      { "name": "Påskedag", "opentime": "07:00", "closeTime": "22:00",
        "validFrom": "2020-04-01T00:00:00.000Z", "validTo": "2020-04-01T00:00:00.000Z" }
    ],
    "schedules": [
      { "name": "Påskeugen",
        "validFrom": "2020-04-06T00:00:00.000Z", "validTo": "2020-04-12T00:00:00.000Z",
        "monday":    { "opentime": "07:00", "closeTime": "22:00" },
        "tuesday":   { "opentime": "07:00", "closeTime": "22:00" }
        // … wednesday … sunday, same shape
      }
    ]
  }
}
```

Note the casing: `opentime` (lower-case t) and `closeTime`.

### 6.4 Store object

```jsonc
{
  "id": "efba0457-090e-4132-81ba-c72b4c8e7fee",
  "name": "Bilka Esbjerg",
  "brand": "netto",
  "address": { "street": "…", "zip": "1161", "city": "København K", "country": "DK" },
  "coordinates": [10.104849, 56.182413],   // [longitude, latitude]
  "distance_km": 3.5,                      // null unless geo search
  "type": "Point",
  "phoneNumber": "87455000",
  "sapSiteId": "1651",
  "vikingStoreId": "5251",
  "created": "…", "modified": "…",
  "hours": [
    { "date": "…", "type": "store", "open": "…", "close": "…",   // open/close nullable
      "closed": false,
      "customerFlow": [0, 0, 0, 0, 0, 0, 0, 0.12, 0.69, 0.72, …] }
  ],
  "attributes": { … }
}
```

- `hours` mixes all hour types (store, bakery, garden centre, …) in one array.
- `customerFlow` (some stores): 24 floats in [0, 1], one per hour from
  midnight, predicting how busy the store is. Higher = busier.

Attributes (each is also a query filter; Boolean unless noted):

`babyChanging`, `bakery`, `carlsJunior`, `clickAndCollect`,
`enablingFacilities`, `evChargingAvailable`, `flowers`, `foodClickAndCollect`,
`garden`, `holidayOpen`, `nonFood`, `open247`, `parking`,
`parkingRestrictions`, `petFood`, `pharmacy`, `scanAndGo`, `starbucks`,
`swipBox`, `wc`, `wifi`, plus:

- `smileyscheme` — string, the store's id in the Danish food-inspection smiley
  scheme.
- `evChargers` — array of `{ "ID": "Clever01", "value": "CleverSuper" }`
  (response only).
- `spotFood`, `spotNonfood` — listed in the guide only.

**Filter semantics:** an attribute filter returns only stores where the
attribute is defined and equal to the value. Stores without the attribute are
excluded from both `=true` and `=false`. Never read absence from a filtered
result as `false`.

### 6.5 Undocumented operations

`GET /v2/stores/feed` and `GET /v2/stores/schedules/list` are in the catalog,
but their specifications fail to load in the portal. Treat as unknown.

---

## 7. Product Suggestions API (Beta)

Search and recommendations over Bilka ToGo products. Results are ordered by
descending relevance.

| Endpoint | Params | Response |
|----------|--------|----------|
| `GET /v1/product-suggestions/relevant-products` | `query` (required, e.g. `mælk`); `limit` (optional, an upper bound, not a guarantee) | **object** `{ "suggestions": [ … ] }` |
| `GET /v1/product-suggestions/similar-products` | `productId` (required, e.g. `39386`) | bare **array** |
| `GET /v1/product-suggestions/frequently-bought-together` | `productId` (required) | bare **array** |

The same three paths also exist under `/v1-beta/…` (beta environment). Which
one a key can call depends on the environment chosen when the key was created.

Statuses: 200, 400, 401, 404, 500, 502, 503.

```json
{
  "title": "HAVREGRYN ØKO OG GLUTENFRI 1000G",
  "id": "29437701",
  "prod_id": "43322",
  "price": 28.95,
  "description": "havregryn øko og glutenfri 1000g",
  "link": "https://www.bilkatogo.dk/produkt/43322/",
  "img": "https://image.prod.iposeninfra.com/bilkaimg.php?pid=43322&imgType=jpeg"
}
```

Gotchas:

- `productId` is the Bilka ToGo e-commerce id, i.e. the item's `prod_id`
  (the number in `link`), not `id`.
- The response envelope differs between `relevant-products` and the other two.
- No EAN is returned, so results cannot be joined to Food Waste or Products
  EAN by barcode.
- URL-encode `query` (`mælk` → `m%C3%A6lk`).

---

## 8. Holidays API

Public holidays and special days for Denmark, Germany and Poland; past,
present and future. Several holidays may share a date.

### 8.1 `GET /v1/holidays`

| Param | In | Notes |
|-------|----|-------|
| `startDate` | query | `YYYY-MM-DD`; default today |
| `endDate` | query | `YYYY-MM-DD`; default 12 months after the start |
| `country` | query | `dk` (default), `de`, `pl` |
| `region` | query | only with `country=de`. German state code with or without the `de-` prefix: `bw`, `by`, `be`, `bb`, `hb`, `hh`, `he`, `mv`, `ni`, `nw`, `rp`, `sl`, `sn`, `st`, `sh`, `th`. Omitted = all regions. |
| `translation` | query | language of the names: `da-dk` (default) or `en-us`. Changes names only, not which holidays are returned. |
| `If-None-Match` | header | ETag from an earlier response |

Statuses: 200 (with a weak `ETag` response header), 304 unchanged, 400.

```json
[ { "date": "2018-01-01", "name": "Nytårsdag", "nationalHoliday": true } ]
```

`nationalHoliday` is true for public holidays and false for observances
(e.g. Valentine's Day, Black Friday). `date` format **[conflict]**: the guide
shows `YYYY-MM-DD`, the OAS example shows a full ISO timestamp — parse both.

### 8.2 `GET /v1/holidays/is-holiday?date=YYYY-MM-DD`

**[conflict]** The guide shows a JSON boolean body (`true`). The OAS defines
200 = the date is a holiday and **404 = the date is not a holiday**. Handle
both: treat 404 as "not a holiday", and on 200 read the body if present.

---

## 9. Jobs API

More than 3,000 open positions across Denmark, Germany and Poland.

### 9.1 `GET /v1/jobs` — list

| Param | Notes |
|-------|-------|
| `brand` | `bilka`, `br`, `carlsjr`, `foetex`, `hugoboss`, `matinique`, `netto`, `salling`, `sallinggroup`, `starbucks`, `storcenternord`. Case-insensitive. |
| `categories` | comma-separated; matches any. Values in 9.4. |
| `country` | `DK`, `DE`, `PL` |
| `region` | region name, e.g. `hovedstaden` (list via `/v1/jobs/regions`) |
| `city` | exact, case-insensitive match |
| `zip` | local postal code |
| `employmentType` | `fullTime`, `partTime` |
| `jobLevel` | `apprentice`, `employee`, `employeeUnder18`, `graduate`, `internship`, `manager` |
| `fields` | csv field selection |
| `page`, `per_page` | defaults 1 and 10 |

The guide's example uses singular `category=`; the OAS parameter is
`categories`. Use `categories`.

Statuses: 200, 400, 401, 503. Job objects are large — always pass `fields`
and `per_page` on broad queries.

### 9.2 `GET /v1/jobs/{id}` — one job

`id` is the job's `id` field. Statuses: 200, 400, 401, 404, 503.

### 9.3 `GET /v1/jobs/{brand}` — list for one brand

Same query parameters as 9.1 without `brand`. Path values: `bilka`, `br`,
`carlsjr`, `dansksupermarked`, `foetex`, `netto`, `salling`, `sallinggroup`,
`starbucks`, `hugoboss`, `matinique`. Prefer `GET /v1/jobs?brand=…`, which is
unambiguous.

### 9.4 Lookup lists

| Endpoint | Item shape |
|----------|------------|
| `GET /v1/jobs/categories` | `{ "categoryName", "displayName", "language" }` |
| `GET /v1/jobs/levels` | `{ "levelName", "displayName", "language" }` |
| `GET /v1/jobs/types` | `{ "typeName", "displayName", "language" }` |
| `GET /v1/jobs/regions` | `{ "regionName", "displayName", "country" }` |

OAS parameters: `sort` (field name) and `fields`. The guide filters with
`?language=` (`en`, `da`, `sv`, `de`, `pl`) and, for regions, `?country=`
(`dk`, `se`, `de`, `pl`). Statuses: 200, 404, 500.

Category values: `administration`, `baker`, `businessDevelopment`, `butcher`,
`cafeRestaurant`, `cashier`, `communication`, `customerInsight`,
`customerService`, `customerServices`, `designAndConstruction`, `digital`,
`distributionAndWarehouse`, `eCommerce`, `finance`, `humanResources`,
`internationalSourcing`, `inventory`, `it`, `legal`, `logisticsSupplyChain`,
`marketing`, `mercer`, `privateLabel`, `procurementAndPurchasingFreshFood`,
`procurementAndPurchasingGrocery`, `procurementAndPurchasingNonFood`,
`procurementAndPurchasingTextile`, `projectProcessManagement`,
`propertyDevelopment`, `quality`, `salesElectronics`, `salesFood`,
`salesFruitVegetables`, `salesGeneral`, `salesHouseGarden`, `salesNearfood`,
`salesNonfood`, `salesOperations`, `salesTextile`,
`spaceManagementAndPromotion`, `technicalServicesFacility`,
`warehouseGoodsHandling`.

### 9.5 Job object

```jsonc
{
  "id": "fcfb898a-8ac4-4e20-8699-597cf662545a",
  "requisitionId": "133525",
  "datasourceId": "12345",
  "title": "1. assistent - Højbjerg",
  "description": "<p>…</p>",            // HTML, often with entities
  "brand": "bilka",
  "address": { "street": "…", "zip": "1161", "city": "Aarhus", "country": "DK" },
  "country": "DE",
  "region": "brandenburg",
  "categories": ["butcher"],
  "employmentType": "fullTime",
  "jobLevel": "employee",
  "jobType": null,
  "hours": "40",                        // string
  "start": "2023-06-01T00:00:00.000Z",
  "end": "2023-10-31T00:00:00.000Z",    // nullable
  "external": true,
  "premium": true,
  "trainee": true,
  "unsolicited": true,
  "applicationLink": "https://…",
  "url": "https://sallinggroup.com/job/ledige-stillinger/…",
  "published": "…", "created": "…", "modified": "…"
}
```

---

## 10. Products EAN API

Basic in-store price information for a product, looked up by barcode.
**Back-end keys only. Educational, non-commercial use only. 100 requests/day.**
Requires requesting access in the catalog.

### `GET /v2/products/{ean}?storeId=<store uuid>`

| Param | In | Notes |
|-------|----|-------|
| `ean` | path, required | product barcode |
| `storeId` | query, **required** | prices differ per store; id from the Stores API |

Statuses: 200, 401, 403, 404 product not found, 429, 500, 504.

```jsonc
{
  "instore": {
    "ean": "6935364070199",
    "name": "TL-WA850RE",
    "description": "TP-LINK",
    "price": 13.95,
    "unit": "l",
    "unitPrice": 13.95,
    "contents": 1,
    "contentsUnit": "l",
    "deposit": { "price": 3, "text": "+ PANT" },      // bottle deposit
    "campaign": {                                     // current promotion, if any
      "displayText": "2 for 24.00",
      "text": "Varen er i øjeblikket på kampagne",
      "groupDisplayText": "Vælg frit mellem alle 0100366062",
      "price": 24, "quantity": 2,
      "unit": "l", "unitPrice": 12,
      "contents": 1, "contentsUnit": "l",
      "fromDate": "2019-11-15T00:00:00.000Z",
      "toDate": "2019-11-21T00:00:00.000Z",
      "showSavedMessage": false
    }
  },
  "webshop": null
}
```

With 100 calls a day, cache every result (including 404s) by `ean` + `storeId`.

---

## 11. Recommendations API

Most-bought product feeds from the online stores. **Back-end keys only.
Educational, non-commercial use only. 100 requests/day.**

- `GET /v1/recommendations/most-bought/bilkatogo/feed`
- `GET /v1/recommendations/most-bought/foetexplus/feed`
- `GET /v1/recommendations/most-bought/nettoplus/feed`

No parameters. The portal provides no documentation for these, so the response
shape is unknown: fetch once, inspect, and cache.

---

## 12. Rules for an agent using these APIs

1. Send `Authorization: Bearer <token>`. Never log the token or put it in a URL.
2. Budget calls per API (section 1). Food Waste, Product Suggestions, Products
   EAN and Recommendations allow only 100 a day: cache, never poll.
3. For Food Waste and Product Suggestions, send requests one at a time and
   wait `Retry-After` seconds between them; bursts quarantine the IP.
4. On 429, sleep for `Retry-After`. Do not retry early.
5. Products EAN and Recommendations keys must stay on a server.
6. "Deals near me" is a single call:
   `GET /v1/food-waste?geo=<lat>,<lng>&radius=<km>`. Keep the radius small
   (20-store cap). No separate Stores call is needed.
7. To add regular prices to a clearance item, call Products EAN with
   `product.ean` and the store's `id` — sparingly.
8. Request `geo` is `lat,lng`; response `coordinates` is `[lng, lat]`.
9. On Stores and Jobs, pass `fields` and `per_page`, follow the `Link` header,
   and use `X-Total-Count` for totals.
10. `city` filters need the exact city string; prefer `zip` or `geo`.
11. Branch on HTTP status; error bodies differ per API.
12. Anything marked **[conflict]** or "undocumented": verify with one request
    before building on it.

---

## Sources

All from https://developer.sallinggroup.dev:

- API reference guides: `/apireference/{food-waste, product-suggestions,
  stores, holidays, jobs, authentication, choosing-authentication-type,
  pagination, spike-protection, quota, client-errors,
  cross-origin-resource-sharing, front-end, back-end}`
- API Catalog (signed in): overview and OpenAPI spec pages for Food Waste,
  Stores, Product Suggestions (Beta), Holidays, Jobs, Products EAN and
  Recommendations.
