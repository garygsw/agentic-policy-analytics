# Data sources

Where the data comes from, how it is fetched and cleaned, and what is mock. Four kinds of source feed the agents:

| Kind | Source | Format | Access |
|---|---|---|---|
| Statistics | data.gov.sg (74 agencies, 4,655 datasets) | CSV via a JSON API | live, cached, snapshot fallback |
| Statistics | SingStat Table Builder (Department of Statistics) | JSON API | live, cached, snapshot fallback |
| Internal data (mock) | a fictional agency's system | Excel workbook | local file |
| Documents | MOM, MOF, PMO, MOH and URA websites | HTML pages | crawled by the ingest service into a search index |
| Recent context | the web, through Tavily (government sites by default) | search API | live, per question |

## 1. Curated datasets

19 datasets have hand-written cleaners, summing rules and caveats (`backend/app/data/registry.py`). Their
cleaned snapshots are committed in `backend/data/snapshots/`. Each comes with a `.json` file holding its quality
report and provenance.

| Key | Dataset | Publisher | Source and ID | Frequency | Coverage |
|---|---|---|---|---|---|
| `employment_by_industry` | Employed Residents by Industry, Age and Sex | Ministry of Manpower | data.gov.sg `d_a7ece9330c024a90b258776e69c2ae00` | annual | 2010–2025 |
| `retrenchment_by_industry` | Retrenched Employees by Industry, Quarterly | Ministry of Manpower | data.gov.sg `d_61d92d31ca400be135190614277da825` | quarterly | 1998-Q1–2026-Q2 |
| `gdp_by_industry` | GDP at Current Prices by Industry (SSIC 2020), Annual | Department of Statistics | SingStat `M015731` | annual | 1960–2025 |
| `ict_manpower` | Employed ICT Manpower | IMDA | data.gov.sg `d_77fa870aa3afb10a1d133b20e9651b6e` | annual | 1999–2018 |
| `tech_professionals` | Employed Tech and Media Professionals, Annual | IMDA (via SingStat) | data.gov.sg `d_52c4d067d88a359917e5f6d9cd60766e` | annual | 2017–2025 |
| `infocomm_revenue` | Revenue of Infocomm Industry by Market Segment | IMDA | data.gov.sg `d_c819e013e5cea669f71872b6cb5fddfe` | annual | 2001–2017 |
| `internal_tech_programmes` | Tech Talent Programme Placements (**synthetic**) | Internal agency system (mock) | `tech_talent_programmes.xlsx` | annual | 2018–2025 |
| `hdb_resale_transactions` | HDB Resale Flat Prices (monthly medians from transactions) | HDB | data.gov.sg `d_8b84c4ee58e3cfc0ece0d773c8ca6abc` | monthly | 2017-01–2026-10 |
| `hdb_resale_price_index` | HDB Resale Price Index (1Q2009 = 100) | HDB | data.gov.sg `d_14f63e595975691e7c24a27ae4c07c79` | quarterly | 1990-Q1–2026-Q2 |
| `hdb_median_resale_by_town` | Median Resale Prices by Town and Flat Type | HDB | data.gov.sg `d_b51323a474ba789fb4cc3db58a3116d4` | quarterly | 2007-Q2–2026-Q2 |
| `cea_transactions` | CEA Salespersons' Residential Transaction Records (monthly counts) | Council for Estate Agencies | data.gov.sg `d_ee7e46d3c57f7865790704632b0aef71` | monthly | 2017-01–2026-09 |
| `private_residential_transactions` | Private Residential Property Transactions, Quarterly | URA | data.gov.sg `d_7c69c943d5f0d89d6a9a773d2b51f337` | quarterly | 1999-Q4–2026-Q2 |
| `new_flat_bookings` | Bookings for New HDB Flats | HDB | data.gov.sg `d_e6079cb5bf0c2450372b1054f37e6e79` | financial year | FY2008–FY2024 |
| `hdb_completions` | HDB Blocks and Flats by Year Completed and Town | HDB | data.gov.sg `d_17f5382f26140b1fdae0ba2ef6239d2f` | annual | 1937–2026 |
| `coe_bidding_results` | COE Bidding Results | LTA | data.gov.sg `d_69b3380ad7e51aff3a7dcc84eba52b8a` | monthly | 2010-01–2026-10 |
| `rail_stations` | MRT and LRT Stations: Master Plan 2025 and Open Stations | URA; LTA | data.gov.sg `d_6f5107a8cf6c9a32439cffbfa9013423`, `d_b39d3a0871985372d7e1637193335da5` | none | stations (planned and open) |
| `hospital_admissions` | Hospital Admissions and Public Sector Outpatient Attendances | MOH | data.gov.sg `d_a5267c58f60b20f8e04576261abfac93` | annual | 1984–2020 |
| `life_expectancy` | Life Expectancy at Birth | MOH | data.gov.sg `d_e8e0db9cbd76e12eaa2bf071cc9ce964` | annual | 1990–2021 |
| `top_hospitalisation_conditions` | Top 10 Conditions of Hospitalisation | MOH | data.gov.sg `d_5961507eed5bede3dc9ea02432153de4` | annual | 1990–2024 |

Coverage is that of the committed snapshots (14 Oct 2026). Live loads may extend it.

**Every other data.gov.sg dataset** is searchable and loadable too. The catalogue's metadata is committed
(`backend/data/catalogue/datagovsg.jsonl.gz`) and indexed for hybrid search. The extraction agent uses it when no
curated dataset fits. These datasets have no hand-written cleaner: their shape (period column, dimensions,
measures) is inferred, high-cardinality text columns such as names are dropped, and they are marked uncurated.

## 2. APIs used

**data.gov.sg** (`backend/app/data/sources.py`, no key needed):

| Endpoint | Purpose |
|---|---|
| `GET https://api-open.data.gov.sg/v1/public/api/datasets/{id}/poll-download` | Ask for a download link. Large files are prepared asynchronously, so the client polls until a URL is returned, then downloads the CSV. |
| `GET https://api-production.data.gov.sg/v2/public/api/datasets/{id}/metadata` | A dataset's metadata: name, agency, update date. |
| `GET https://api-production.data.gov.sg/v2/public/api/datasets?page=N` | The catalogue, page by page: `python -m app.data.catalogue refresh`. |

**SingStat Table Builder:**
`GET https://tablebuilder.singstat.gov.sg/api/table/tabledata/{table_id}` returns a table as JSON. Series are
nested by numbered rows (1, 1.1, 1.1.1), and the cleaner turns that numbering into hierarchy levels.

**Tavily** (optional, `TAVILY_API_KEY`): `POST https://api.tavily.com/search`, restricted to 21 government
domains unless the user widens the scope per request. Tavily's domain filter needs exact domains (`*.gov.sg`
matches nothing), so the list is explicit (`backend/app/rag/web.py`).

**Failures.** Every request goes through one client (`backend/app/data/http.py`):
- **Rate limits and server errors:** HTTP 429 and 5xx are retried with backoff (10, 20 and 40 s).
- **Client errors:** other 4xx responses are not retried.
- **When retries run out:** the data store falls back to a stale cache, then to the snapshot. The report says
  which copy was used and from when.
- **Demo switch:** `FORCE_SOURCE_FAIL=true` skips live sources, to demonstrate the fallback.

## 3. Cleaning and quality

Every dataset is reshaped into one tidy form, `period | dimensions | measure | value | unit`. Every row dropped or
changed is counted in a quality report (`backend/app/data/cleaning.py`):

| Check | Example |
|---|---|
| Periods normalised | `2024 1Q`, `1Q 2024` and `2024-Q1` all become `2024-Q1`; `OCT-2017` becomes `2017-10`; financial years become `FY2024` |
| Invalid periods dropped and counted | `Q5 2024`, `total` |
| Numbers parsed | `1,234`, `$5.6` and `12%`; placeholders (`na`, `-`, `TBC`) counted as missing or unparseable, or as nil where the publisher says so (MOM's `-`) |
| Duplicate rows removed and counted | counted on source rows, before reshaping |
| Total rows removed | so sums over a dimension do not count a total twice |
| Labels differing only in case merged | HDB renamed "Ang Mo Kio" to "ANG MO KIO" in 2019, which split every town's series |
| Personal data dropped | CEA salesperson names and registration numbers, on load |
| Coverage recorded | first and last period |

Each curated dataset also has **summing rules**: the dimensions across which a measure may be added up.
- Employment can be summed across industries, sex and age.
- A median or a price index is never summed.
- Retrenchment labels mix broad industry groups with their sub-industries, so they are never added together.

The analytics tools enforce these rules and say which dimension needs a filter. Tests
covering all of this are listed in [TESTING.md](TESTING.md).

## 4. Mock internal data

The brief allows mock internal databases. This project includes one, so that the system shows it can join an
organisation's own records to public data, and how it handles their typical problems.

- **What it is:** `backend/data/internal/tech_talent_programmes.xlsx`, a workbook of a **fictional** agency's
  tech-talent programmes, 2018–2025. It records participants, placements within six months and median starting
  salary, by programme (graduate traineeship, industry upskilling, mid-career conversion) and technical track.
  Its README sheet and the dataset's title both say it is synthetic.
- **How it is made:** `uv run python scripts/make_internal_mock.py`, with a fixed random seed, so it is
  reproducible. Growth is steady, with a dip in 2020.
- **Deliberate problems,** like a real export's: a duplicate record, a missing value (`n.a.`), a non-numeric
  value (`TBC`), and a "Total" row per year. The cleaner catches all four and reports them, and the agents cite
  the quality report in caveats.
- **How it is read:** like an internal system's export. The "internal" source reads the file and reports it as
  unavailable if the file is missing, which exercises the same fallback as a live source. A JSON API for it was
  first planned (D-005) but not built, because the workbook already covers the Excel format and the source
  interface is the same (D-033).

## 5. Documents

The ingest service (`backend/app/rag/`, run on a cron schedule) crawls agency sites into the document index:

| Source | Sitemap | Pages kept |
|---|---|---|
| Ministry of Manpower | `https://www.mom.gov.sg/newsroom.xml` | press releases, speeches, parliamentary replies, press replies, announcements, statements |
| Ministry of Finance | `https://www.mof.gov.sg/sitemap.xml` | newsroom |
| Prime Minister's Office | `https://www.pmo.gov.sg/sitemap.xml` | newsroom |
| Ministry of Health | `https://www.moh.gov.sg/sitemap.xml` | newsroom |
| Urban Redevelopment Authority | `https://www.ura.gov.sg/sitemap.xml` | media releases, articles, Master Plan and Long-Term Plan pages |

- **Crawling rules:** pages changed since 2024 (`INGEST_SINCE`), newest first, up to 500 per agency
  (`INGEST_LIMIT_PER_SOURCE`). robots.txt is honoured, with a one-second delay between requests.
- **Index on 14 Oct 2026:** 2,990 documents and 14,363 passages, plus the dataset catalogue. The index is built
  locally and never committed.
- **Not included** (D-014, D-025):
  - **Parliamentary Hansard:** its search API returns errors, and the site blocks automated access.
  - **Commercial news:** copyright.
  - **Sites that block automated access:** gov.sg, MOE, HDB and Statutes Online.
  - **The Monetary Authority of Singapore's site:** it returns a "service unavailable" page to any non-browser
    client. Getting around that would mean evading its access controls, so MAS pages are reached through web
    search instead.
  - **Services that need their own registered key** (LTA DataMall, URA's data API, OneMap): data.gov.sg already
    carries their datasets.

## 6. Licences and attribution

- **data.gov.sg** datasets are used under the
  [Singapore Open Data Licence v1.0](https://data.gov.sg/open-data-licence). Each snapshot's `.json` file names
  the publisher and links to the source dataset. Snapshots are processed data: personal columns are removed, and
  some datasets are aggregated (e.g. monthly medians from individual transactions).
- **SingStat:** the GDP table comes from the Department of Statistics' Table Builder API. The same table is
  published on data.gov.sg (`d_d0db56a7c201c9a1e42c846d4fe6da8f`) under the licence above.
- **Agency web pages** are fetched at ingest time and stored only in the local index. Reports quote short passages
  and link to the source.
- **The internal workbook** is synthetic, made for this project, and not an official statistic.
