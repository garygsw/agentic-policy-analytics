"""The curated datasets: where each comes from, how it is cleaned, and the caveats an analyst must know.

Each cleaner takes the raw bytes of its source files and returns the tidy table plus a QualityReport.
Notes are shown to the agents, so they reflect what was found by reading the actual data.
"""
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from app.data.cleaning import Frequency, QualityReport, tidy

Raw = dict[str, bytes]
Cleaner = Callable[[Raw], tuple[pd.DataFrame, QualityReport]]


@dataclass(frozen=True)
class CuratedDataset:
    key: str
    title: str
    agency: str
    topic: str
    description: str
    source: str                   # datagovsg | singstat | internal
    source_ids: tuple[str, ...]
    frequency: Frequency
    clean: Cleaner
    notes: str = ""
    # Which measures may be added up, and over which dimensions. Anything not listed (medians, prices, indices,
    # rates, nested or overlapping categories) can only be read one combination at a time.
    summable: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _csv(raw: Raw, source_id: str) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(raw[source_id]), dtype=str, keep_default_na=False)


# -- employment and the digital economy -------------------------------------------------------------------
EMPLOYED = "d_a7ece9330c024a90b258776e69c2ae00"
RETRENCHED = "d_61d92d31ca400be135190614277da825"
ICT_MANPOWER = "d_77fa870aa3afb10a1d133b20e9651b6e"
INFOCOMM_REVENUE = "d_c819e013e5cea669f71872b6cb5fddfe"
GDP_TABLE = "M015731"


def clean_employment(raw: Raw):
    df = _csv(raw, EMPLOYED).rename(columns={"industry2": "industry", "industry1": "sector"})
    return tidy(df, "employment_by_industry", period="year", frequency="annual",
                dims=["sector", "industry", "sex", "age"], measures={"employed": "persons"})


def clean_retrenchment(raw: Raw):
    return tidy(_csv(raw, RETRENCHED), "retrenchment_by_industry", period="quarter", frequency="quarterly",
                dims=["industry"], measures={"retrench": "persons"}, zero_markers=("-",))


def clean_gdp(raw: Raw):
    """SingStat JSON: series rows (numbered 1, 1.1, 1.1.1 ... by nesting) with one column per year."""
    data = json.loads(raw[GDP_TABLE])["Data"]
    records = [{"industry": row["rowText"].strip(), "level": str(row["seriesNo"].count(".") + 1),
                "year": col["key"], "gdp": col["value"]}
               for row in data["row"] for col in row["columns"]]
    frame, report = tidy(pd.DataFrame(records), "gdp_by_industry", period="year", frequency="annual",
                         dims=["industry", "level"], measures={"gdp": "S$ million"})
    report.notes.append(f"SingStat last updated {data.get('dataLastUpdated')}")
    return frame, report


def clean_ict_manpower(raw: Raw):
    return tidy(_csv(raw, ICT_MANPOWER), "ict_manpower", period="year", frequency="annual", dims=[],
                measures={"manpower": "persons", "growth": "%"})


def clean_infocomm_revenue(raw: Raw):
    df = _csv(raw, INFOCOMM_REVENUE)
    df["segment"] = df["segment"].str.replace("telecommuncation", "telecommunication").str.replace("_", " ")
    frame, report = tidy(df, "infocomm_revenue", period="year", frequency="annual", dims=["segment"],
                         measures={"infocomm_industry_revenue": "S$ billion"})
    report.notes.append("fixed source typo 'telecommuncation' in segment names")
    return frame, report


def clean_internal_programmes(raw: Raw):
    df = pd.read_excel(io.BytesIO(raw["tech_talent_programmes.xlsx"]), sheet_name="placements", dtype=str)
    return tidy(df, "internal_tech_programmes", period="year", frequency="annual", dims=["programme", "track"],
                measures={"participants": "persons", "placed_within_6_months": "persons",
                          "median_starting_salary_sgd": "S$ per month"}, total_labels=("Total",))


# -- housing -------------------------------------------------------------------------------------------------
RESALE = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
RESALE_INDEX = "d_14f63e595975691e7c24a27ae4c07c79"
MEDIAN_RESALE = "d_b51323a474ba789fb4cc3db58a3116d4"
CEA = "d_ee7e46d3c57f7865790704632b0aef71"
PRIVATE_TXN = "d_7c69c943d5f0d89d6a9a773d2b51f337"
NEW_FLAT_BOOKINGS = "d_e6079cb5bf0c2450372b1054f37e6e79"
HDB_PROPERTY = "d_17f5382f26140b1fdae0ba2ef6239d2f"
HDB_TOWNS = {"AMK": "Ang Mo Kio", "BB": "Bukit Batok", "BD": "Bedok", "BH": "Bishan", "BM": "Bukit Merah",
             "BP": "Bukit Panjang", "BT": "Bukit Timah", "CCK": "Choa Chu Kang", "CL": "Clementi",
             "CT": "Central Area", "GL": "Geylang", "HG": "Hougang", "JE": "Jurong East", "JW": "Jurong West",
             "KWN": "Kallang/Whampoa", "MP": "Marine Parade", "PG": "Punggol", "PRC": "Pasir Ris",
             "QT": "Queenstown", "SB": "Sembawang", "SGN": "Serangoon", "SK": "Sengkang", "TAP": "Tampines",
             "TG": "Tengah", "TP": "Toa Payoh", "WL": "Woodlands", "YS": "Yishun"}


def _aggregate(df: pd.DataFrame, dataset_id: str, rows_in: int, *, frequency: Frequency, dims: list[str],
               measures: dict[str, str], note: str) -> tuple[pd.DataFrame, QualityReport]:
    frame, report = tidy(df, dataset_id, period="period", frequency=frequency, dims=dims, measures=measures)
    report.rows_in = rows_in
    report.notes.append(note)
    return frame, report


def clean_resale(raw: Raw):
    """24 MB of individual transactions, aggregated to monthly medians per town and flat type."""
    df = _csv(raw, RESALE)
    df["resale_price"] = pd.to_numeric(df["resale_price"], errors="coerce")
    df["price_per_sqm"] = df["resale_price"] / pd.to_numeric(df["floor_area_sqm"], errors="coerce")
    grouped = df.groupby(["month", "town", "flat_type"]).agg(
        median_resale_price=("resale_price", "median"), median_price_per_sqm=("price_per_sqm", "median"),
        transactions=("resale_price", "size")).reset_index().rename(columns={"month": "period"})
    return _aggregate(grouped, "hdb_resale_transactions", len(df), frequency="monthly", dims=["town", "flat_type"],
                      measures={"median_resale_price": "S$", "median_price_per_sqm": "S$ per sqm",
                                "transactions": "transactions"},
                      note=f"aggregated from {len(df):,} individual resale transactions")


def clean_resale_index(raw: Raw):
    return tidy(_csv(raw, RESALE_INDEX), "hdb_resale_price_index", period="quarter", frequency="quarterly", dims=[],
                measures={"index": "index (1Q2009 = 100)"})


def clean_median_resale(raw: Raw):
    return tidy(_csv(raw, MEDIAN_RESALE), "hdb_median_resale_by_town", period="quarter", frequency="quarterly",
                dims=["town", "flat_type"], measures={"price": "S$"})


def clean_cea(raw: Raw):
    """134 MB of salesperson records. Names and registration numbers identify individuals and are never needed
    for policy questions, so they are dropped before anything else happens."""
    df = _csv(raw, CEA)
    personal = [c for c in ("salesperson_name", "salesperson_reg_num") if c in df.columns]
    df = df.drop(columns=personal)
    df["period"] = df["transaction_date"]
    # HDB records name the town; private property records give a postal district instead.
    df["location"] = df["town"].where(df["town"] != "-", "District " + df["district"])
    grouped = (df.groupby(["period", "property_type", "transaction_type", "represented", "location"])
               .size().rename("records").reset_index())
    frame, report = _aggregate(grouped, "cea_transactions", len(df), frequency="monthly",
                               dims=["property_type", "transaction_type", "represented", "location"],
                               measures={"records": "salesperson records"},
                               note=f"aggregated from {len(df):,} salesperson records")
    report.personal_data_columns_dropped = personal
    return frame, report


def clean_private_transactions(raw: Raw):
    df = _csv(raw, PRIVATE_TXN)
    df["sale_status"] = df["sale_status"].replace({"na": "not applicable"})
    return tidy(df, "private_residential_transactions", period="quarter", frequency="quarterly",
                dims=["type_of_sale", "sale_status"], measures={"units": "units"})


def clean_new_flat_bookings(raw: Raw):
    return tidy(_csv(raw, NEW_FLAT_BOOKINGS), "new_flat_bookings", period="financial_year",
                frequency="financial_year", dims=[], measures={"no_of_units": "flats"})


def clean_hdb_completions(raw: Raw):
    """Blocks by completion year and town: where new housing estates are being built."""
    df = _csv(raw, HDB_PROPERTY)
    df = df[df["residential"] == "Y"]
    df["town"] = df["bldg_contract_town"].map(HDB_TOWNS).fillna(df["bldg_contract_town"])
    df["total_dwelling_units"] = pd.to_numeric(df["total_dwelling_units"], errors="coerce")
    grouped = (df.groupby(["year_completed", "town"])
               .agg(blocks=("blk_no", "size"), dwelling_units=("total_dwelling_units", "sum"))
               .reset_index().rename(columns={"year_completed": "period"}))
    return _aggregate(grouped, "hdb_completions", len(df), frequency="annual", dims=["town"],
                      measures={"blocks": "blocks", "dwelling_units": "flats"},
                      note=f"aggregated from {len(df):,} existing residential blocks")


# -- transport -----------------------------------------------------------------------------------------------
COE = "d_69b3380ad7e51aff3a7dcc84eba52b8a"
COE_DIMS = ("vehicle_class", "bidding_exercise")
RAIL_PLAN = "d_6f5107a8cf6c9a32439cffbfa9013423"
MRT_EXITS = "d_b39d3a0871985372d7e1637193335da5"


def clean_coe(raw: Raw):
    df = _csv(raw, COE)
    df["bidding_exercise"] = "exercise " + df["bidding_no"]
    return tidy(df, "coe_bidding_results", period="month", frequency="monthly",
                dims=["vehicle_class", "bidding_exercise"],
                measures={"premium": "S$", "quota": "certificates", "bids_received": "bids",
                          "bids_success": "bids"})


# LTA's exit layer names a few stations by station code. These are the public codes of those stations.
EXIT_CODE_NAMES = {"CC9": "PAYA LEBAR", "CC30": "KEPPEL", "CC31": "CANTONMENT", "CC32": "PRINCE EDWARD ROAD",
                   "DT4": "HUME", "DT18": "TELOK AYER", "NE18": "PUNGGOL COAST"}
NAME_ALIASES = {"GARDEN BY THE BAY": "GARDENS BY THE BAY", "JELEPANG": "JELAPANG"}  # spellings differ


def _station(name: str) -> str:
    """One spelling per station across both layers: no suffixes, no hyphens, codes replaced by names."""
    name = " ".join(name.upper().replace("-", " ").split())
    for suffix in (" MRT STATION", " LRT STATION", " STATION", " INTERCHANGE"):
        name = name.removesuffix(suffix)
    name = EXIT_CODE_NAMES.get(name, name)
    return NAME_ALIASES.get(name, name)


def clean_rail_stations(raw: Raw):
    """Stations in the Master Plan 2025 rail layer, marked open if LTA's exit layer has exits for them."""
    plan = json.loads(raw[RAIL_PLAN])["features"]
    exits = json.loads(raw[MRT_EXITS])["features"]
    exit_counts = pd.Series([_station(f["properties"]["STATION_NA"]) for f in exits]).value_counts()
    stations = {}
    for feature in plan:
        p = feature["properties"]
        stations[_station(p["NAME"])] = {"station": _station(p["NAME"]).title(), "rail_type": p["RAIL_TYPE"],
                                         "level": p["GRND_LEVEL"].lower()}
    df = pd.DataFrame(stations.values())
    df["exits"] = [int(exit_counts.get(s.upper(), 0)) for s in df["station"]]
    df["status"] = ["open" if n else "planned (no exits yet)" for n in df["exits"]]
    frame, report = tidy(df, "rail_stations", period=None, frequency="none",
                         dims=["station", "rail_type", "level", "status"], measures={"exits": "exits"})
    report.rows_in = len(plan)
    report.notes.append(f"{len(plan)} plan features merged into {len(df)} stations; "
                        f"{(df['exits'] == 0).sum()} have no exits in LTA's layer")
    return frame, report


# -- health --------------------------------------------------------------------------------------------------
ADMISSIONS = "d_a5267c58f60b20f8e04576261abfac93"
LIFE_EXPECTANCY = "d_e8e0db9cbd76e12eaa2bf071cc9ce964"
TOP_CONDITIONS = "d_5961507eed5bede3dc9ea02432153de4"


def clean_admissions(raw: Raw):
    df = _csv(raw, ADMISSIONS).rename(columns={"level_1": "category"})
    return tidy(df, "hospital_admissions", period="year", frequency="annual", dims=["category"],
                measures={"value": "admissions or attendances"})


def clean_life_expectancy(raw: Raw):
    return tidy(_csv(raw, LIFE_EXPECTANCY), "life_expectancy", period="year", frequency="annual", dims=["sex"],
                measures={"LE_total_population_GBD": "years"})


def clean_top_conditions(raw: Raw):
    df = _csv(raw, TOP_CONDITIONS).rename(columns={"disease_condition": "condition"})
    return tidy(df, "top_hospitalisation_conditions", period="year", frequency="annual",
                dims=["rank", "condition", "icd"], measures={"percentage_discharges": "% of discharges"})


CURATED: dict[str, CuratedDataset] = {d.key: d for d in [
    CuratedDataset("employment_by_industry", "Employed Residents by Industry, Age and Sex", "Ministry of Manpower",
                   "employment", "Annual employed residents by industry (incl. information and communications), "
                   "sector, sex and age group", "datagovsg", (EMPLOYED,), "annual", clean_employment,
                   "Residents only (citizens and PRs); counts rounded to the nearest hundred. Add up across sex and "
                   "age to get an industry total. 'information and communications' is the tech sector.",
                   summable={"employed": ("sector", "industry", "sex", "age")}),
    CuratedDataset("retrenchment_by_industry", "Retrenched Employees by Industry, Quarterly", "Ministry of Manpower",
                   "employment", "Quarterly number of employees retrenched, by industry", "datagovsg", (RETRENCHED,),
                   "quarterly", clean_retrenchment,
                   "Industry labels follow several SSIC revisions and mix broad groups with sub-industries: compare "
                   "one label over time, never add labels together. '-' means nil."),
    CuratedDataset("gdp_by_industry", "GDP at Current Prices by Industry (SSIC 2020), Annual",
                   "Singapore Department of Statistics", "economy",
                   "Annual nominal GDP by industry, including information and communications", "singstat",
                   (GDP_TABLE,), "annual", clean_gdp,
                   "Series are nested (level 1 is total GDP; deeper levels are components): never add across "
                   "levels. Current prices, not adjusted for inflation."),
    CuratedDataset("ict_manpower", "Employed ICT Manpower", "Info-communications Media Development Authority",
                   "digital economy", "Annual employed ICT manpower across all industries, with growth rate",
                   "datagovsg", (ICT_MANPOWER,), "annual", clean_ict_manpower,
                   "Ends in 2018. Counts ICT jobs in every industry, unlike the industry-based employment data."),
    CuratedDataset("infocomm_revenue", "Revenue of Infocomm Industry by Market Segment",
                   "Info-communications Media Development Authority", "digital economy",
                   "Annual infocomm industry revenue by segment (software, IT services, telecommunications...)",
                   "datagovsg", (INFOCOMM_REVENUE,), "annual", clean_infocomm_revenue, "Ends in 2017.",
                   summable={"infocomm_industry_revenue": ("segment",)}),
    CuratedDataset("internal_tech_programmes", "Tech Talent Programme Placements (internal, synthetic)",
                   "Internal agency system (mock)", "employment",
                   "Annual participants, placements within 6 months and median starting salary for tech-talent "
                   "programmes by track", "internal", ("tech_talent_programmes.xlsx",), "annual",
                   clean_internal_programmes, "SYNTHETIC demonstration data, not an official statistic.",
                   summable={"participants": ("programme", "track"), "placed_within_6_months": ("programme", "track")}),
    CuratedDataset("hdb_resale_transactions", "HDB Resale Flat Prices (monthly medians from transactions)",
                   "Housing & Development Board", "housing",
                   "Monthly median resale price, price per square metre and number of transactions by town and "
                   "flat type, from January 2017", "datagovsg", (RESALE,), "monthly", clean_resale,
                   "Medians of transactions registered in each month; small town/flat-type groups are volatile.",
                   summable={"transactions": ("town", "flat_type")}),
    CuratedDataset("hdb_resale_price_index", "HDB Resale Price Index (1Q2009 = 100)", "Housing & Development Board",
                   "housing", "Quarterly index of HDB resale prices", "datagovsg", (RESALE_INDEX,), "quarterly",
                   clean_resale_index),
    CuratedDataset("hdb_median_resale_by_town", "Median Resale Prices by Town and Flat Type",
                   "Housing & Development Board", "housing",
                   "Quarterly median resale prices of registered applications by town and flat type", "datagovsg",
                   (MEDIAN_RESALE,), "quarterly", clean_median_resale,
                   "'-' means too few transactions to publish, and is treated as missing."),
    CuratedDataset("cea_transactions", "CEA Salespersons' Residential Transaction Records (monthly counts)",
                   "Council for Estate Agencies", "housing",
                   "Monthly counts of residential sales and rentals handled by estate agents, by property type, "
                   "transaction type and location (HDB town or postal district)", "datagovsg", (CEA,), "monthly",
                   clean_cea,
                   "Counts are salesperson records, not transactions: a deal with agents on both sides appears "
                   "twice. Salesperson names and registration numbers are removed on load.",
                   summable={"records": ("property_type", "transaction_type", "represented", "location")}),
    CuratedDataset("private_residential_transactions", "Private Residential Property Transactions, Quarterly",
                   "Urban Redevelopment Authority", "housing",
                   "Quarterly private residential units transacted, by type of sale (new sale, resale, sub sale)",
                   "datagovsg", (PRIVATE_TXN,), "quarterly", clean_private_transactions,
                   summable={"units": ("type_of_sale", "sale_status")}),
    CuratedDataset("new_flat_bookings", "Bookings for New HDB Flats", "Housing & Development Board", "housing",
                   "Number of new HDB flats booked per financial year", "datagovsg", (NEW_FLAT_BOOKINGS,),
                   "financial_year", clean_new_flat_bookings),
    CuratedDataset("hdb_completions", "HDB Blocks and Flats by Year Completed and Town",
                   "Housing & Development Board", "housing",
                   "Residential HDB blocks and dwelling units by completion year and town, showing where new "
                   "housing estates are built", "datagovsg", (HDB_PROPERTY,), "annual", clean_hdb_completions,
                   "Built from existing blocks: demolished blocks are missing, so older years are undercounted.",
                   summable={"blocks": ("town",), "dwelling_units": ("town",)}),
    CuratedDataset("coe_bidding_results", "COE Bidding Results", "Land Transport Authority", "transport",
                   "Certificate of Entitlement premiums, quotas and bids by vehicle category, two exercises a month",
                   "datagovsg", (COE,), "monthly", clean_coe,
                   "Category A: cars up to 1,600cc; B: larger cars; C: goods vehicles and buses; D: motorcycles; "
                   "E: open category.",
                   summable=dict.fromkeys(("quota", "bids_received", "bids_success"), COE_DIMS)),
    CuratedDataset("rail_stations", "MRT and LRT Stations: Master Plan 2025 and Open Stations",
                   "Urban Redevelopment Authority; Land Transport Authority", "transport",
                   "Rail stations in the Master Plan 2025, marked open or planned by whether LTA lists exits",
                   "datagovsg", (RAIL_PLAN, MRT_EXITS), "none", clean_rail_stations,
                   "'Planned' means no exits in LTA's layer yet, usually a future station; the Sentosa Express "
                   "stations (Beach, Imbiah, Resorts World) are not in that layer either. Not a time series.",
                   summable={"exits": ("station", "rail_type", "level", "status")}),
    CuratedDataset("hospital_admissions", "Hospital Admissions and Public Sector Outpatient Attendances",
                   "Ministry of Health", "health", "Annual admissions to acute, psychiatric and community hospitals "
                   "and outpatient attendances", "datagovsg", (ADMISSIONS,), "annual", clean_admissions,
                   "Ends in 2020."),
    CuratedDataset("life_expectancy", "Life Expectancy at Birth", "Ministry of Health", "health",
                   "Annual life expectancy at birth by sex", "datagovsg", (LIFE_EXPECTANCY,), "annual",
                   clean_life_expectancy, "Ends in 2021."),
    CuratedDataset("top_hospitalisation_conditions", "Top 10 Conditions of Hospitalisation", "Ministry of Health",
                   "health", "Annual top 10 conditions by share of hospital discharges", "datagovsg",
                   (TOP_CONDITIONS,), "annual", clean_top_conditions,
                   "Disease coding changed from ICD9 to ICD10 over time, so conditions are not always comparable."),
]}
