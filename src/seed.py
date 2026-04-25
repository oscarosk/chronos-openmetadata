"""
Chronos — Day 1 Seed Script
Creates a realistic data platform in OpenMetadata for Chronos to investigate.
Idempotent: safe to re-run.
"""
import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel

load_dotenv()
console = Console()

URL = os.environ["OM_URL"]
TOKEN = os.environ["OM_TOKEN"]
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def put(path, payload):
    r = requests.put(f"{URL}{path}", headers=HEADERS, json=payload)
    if r.status_code >= 400:
        console.print(f"[red]PUT {path} FAILED ({r.status_code}): {r.text[:400]}[/red]")
        sys.exit(1)
    return r.json()

def post(path, payload, allow_conflict=True):
    r = requests.post(f"{URL}{path}", headers=HEADERS, json=payload)
    if r.status_code == 409 and allow_conflict:
        return None
    if r.status_code >= 400:
        console.print(f"[red]POST {path} FAILED ({r.status_code}): {r.text[:400]}[/red]")
        sys.exit(1)
    return r.json()

def get(path):
    r = requests.get(f"{URL}{path}", headers=HEADERS)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        console.print(f"[red]GET {path} FAILED ({r.status_code}): {r.text[:400]}[/red]")
        sys.exit(1)
    return r.json()

def get_by_fqn(entity_path, fqn):
    return get(f"/{entity_path}/name/{fqn}")

# ------------------------------------------------------------
# Auth check
# ------------------------------------------------------------

console.print(Panel.fit("[bold cyan]CHRONOS SEED SCRIPT[/bold cyan]\nStaging a realistic data platform for investigation"))
me = get("/users/loggedInUser")
if not me:
    console.print("[red]❌ Auth failed — check OM_TOKEN in .env[/red]")
    sys.exit(1)
console.print(f"✅ Authenticated as [bold]{me['name']}[/bold]")

# ------------------------------------------------------------
# 1. Database Service
# ------------------------------------------------------------

SERVICE_NAME = "chronos_warehouse"
console.print(f"\n[bold]▶ Creating database service:[/bold] {SERVICE_NAME}")

service_payload = {
    "name": SERVICE_NAME,
    "serviceType": "Mysql",
    "connection": {
        "config": {
            "type": "Mysql",
            "scheme": "mysql+pymysql",
            "username": "chronos",
            "authType": {"password": "stub"},
            "hostPort": "localhost:3306",
        }
    },
    "description": "Chronos demo warehouse — synthetic data platform for investigation demo",
}
put("/services/databaseServices", service_payload)
svc = get_by_fqn("services/databaseServices", SERVICE_NAME)
console.print(f"  ✓ Service ID: {svc['id']}")

# ------------------------------------------------------------
# 2. Database + Schema
# ------------------------------------------------------------

DB_NAME = "analytics_db"
console.print(f"\n[bold]▶ Creating database:[/bold] {DB_NAME}")
put("/databases", {
    "name": DB_NAME,
    "service": SERVICE_NAME,
    "description": "Analytics warehouse",
})
db = get_by_fqn("databases", f"{SERVICE_NAME}.{DB_NAME}")
console.print(f"  ✓ DB ID: {db['id']}")

SCHEMA_NAME = "public"
console.print(f"\n[bold]▶ Creating schema:[/bold] {SCHEMA_NAME}")
put("/databaseSchemas", {
    "name": SCHEMA_NAME,
    "database": f"{SERVICE_NAME}.{DB_NAME}",
})
schema = get_by_fqn("databaseSchemas", f"{SERVICE_NAME}.{DB_NAME}.{SCHEMA_NAME}")
console.print(f"  ✓ Schema ID: {schema['id']}")

# ------------------------------------------------------------
# 3. Tables
# ------------------------------------------------------------

def col(name, dtype="VARCHAR", desc="", length=None):
    c = {
        "name": name,
        "dataType": dtype,
        "dataTypeDisplay": dtype.lower(),
        "description": desc,
    }
    # OpenMetadata requires dataLength for string types
    if dtype.upper() in ("VARCHAR", "CHAR", "BINARY", "VARBINARY"):
        c["dataLength"] = length or 255
    return c

tables = {
    "raw_orders": {
        "description": "Raw orders dump from Shopify. Upstream source.",
        "columns": [
            col("order_id", "BIGINT", "Order identifier"),
            col("customer_identifier", "BIGINT", "Renamed from cust_id on 2026-04-20"),
            col("order_date", "TIMESTAMP", "When the order was placed"),
            col("total_amount", "DECIMAL", "Order total in USD"),
        ],
    },
    "raw_customers": {
        "description": "Raw customer data from CRM",
        "columns": [
            col("customer_id", "BIGINT", "Customer identifier"),
            col("email", "VARCHAR", "Customer email (PII)"),
            col("signup_date", "TIMESTAMP", "Account creation timestamp"),
        ],
    },
    "stg_orders": {
        "description": "Staging table - transforms raw_orders for downstream fact table",
        "columns": [
            col("order_id", "BIGINT", "Order identifier"),
            col("customer_id", "BIGINT", "FK to dim_customer. SELECTED from raw_orders.cust_id (now broken)"),
            col("order_date", "TIMESTAMP"),
            col("total_amount", "DECIMAL"),
        ],
    },
    "dim_customer": {
        "description": "Customer dimension table - tier 1",
        "columns": [
            col("customer_id", "BIGINT", "Primary key"),
            col("email", "VARCHAR", "PII - customer email"),
            col("signup_date", "TIMESTAMP"),
            col("lifetime_value", "DECIMAL", "Derived CLV"),
        ],
    },
    "fact_order": {
        "description": "Core orders fact table - powers Exec Weekly & Revenue Monitor dashboards. Tier 1.",
        "columns": [
            col("order_id", "BIGINT", "Order identifier"),
            col("customer_id", "BIGINT", "FK to dim_customer"),
            col("order_date", "TIMESTAMP"),
            col("total_amount", "DECIMAL", "Order revenue"),
            col("customer_email", "VARCHAR", "Denormalized PII column"),
        ],
    },
}

console.print(f"\n[bold]▶ Creating 5 tables[/bold]")
table_ids = {}
for tname, tdata in tables.items():
    put("/tables", {
        "name": tname,
        "databaseSchema": f"{SERVICE_NAME}.{DB_NAME}.{SCHEMA_NAME}",
        "columns": tdata["columns"],
        "description": tdata["description"],
    })
    t = get_by_fqn("tables", f"{SERVICE_NAME}.{DB_NAME}.{SCHEMA_NAME}.{tname}")
    table_ids[tname] = t["id"]
    console.print(f"  ✓ {tname} — {t['id']}")

# ------------------------------------------------------------
# 4. Lineage
# ------------------------------------------------------------

console.print("\n[bold]▶ Creating lineage edges[/bold]")

def add_edge(from_name, to_name):
    payload = {
        "edge": {
            "fromEntity": {"id": table_ids[from_name], "type": "table"},
            "toEntity": {"id": table_ids[to_name], "type": "table"},
            "lineageDetails": {
                "sqlQuery": f"INSERT INTO {to_name} SELECT * FROM {from_name}",
            },
        }
    }
    r = requests.put(f"{URL}/lineage", headers=HEADERS, json=payload)
    if r.status_code >= 400:
        console.print(f"[red]Lineage {from_name}→{to_name} FAILED: {r.text[:300]}[/red]")
    else:
        console.print(f"  ✓ {from_name} → {to_name}")

add_edge("raw_orders", "stg_orders")
add_edge("stg_orders", "fact_order")
add_edge("raw_customers", "dim_customer")
add_edge("dim_customer", "fact_order")

# ------------------------------------------------------------
# 5. Tier1 tagging
# ------------------------------------------------------------

console.print("\n[bold]▶ Marking fact_order + dim_customer as Tier1[/bold]")
for critical in ["fact_order", "dim_customer"]:
    patch = [
        {
            "op": "add",
            "path": "/tags/0",
            "value": {
                "tagFQN": "Tier.Tier1",
                "labelType": "Manual",
                "state": "Confirmed",
                "source": "Classification",
            },
        }
    ]
    r = requests.patch(
        f"{URL}/tables/{table_ids[critical]}",
        headers={**HEADERS, "Content-Type": "application/json-patch+json"},
        json=patch,
    )
    if r.status_code < 400:
        console.print(f"  ✓ {critical} marked Tier1")
    else:
        console.print(f"  [yellow]⚠ Tier1 on {critical}: {r.status_code}[/yellow]")

# ------------------------------------------------------------
# 5b. Owners — create teams and assign them to critical tables
# ------------------------------------------------------------

console.print("\n[bold]▶ Creating teams + assigning owners[/bold]")

# Create team if doesn't exist
TEAM_NAME = "marketing_data"

r = requests.put(
    f"{URL}/teams",
    headers=HEADERS,
    json={
        "name": TEAM_NAME,
        "displayName": "Marketing Data",
        "description": "Team responsible for marketing & revenue analytics",
        "teamType": "Group",
    },
)
if r.status_code < 400:
    team = r.json()
    console.print(f"  ✓ Team '{TEAM_NAME}' ready")
else:
    console.print(f"  [yellow]⚠ Team create: {r.status_code} {r.text[:200]}[/yellow]")
    team = get_by_fqn("teams", TEAM_NAME)

# Assign team as owner on fact_order and dim_customer
if team:
    for critical in ["fact_order", "dim_customer"]:
        patch = [
            {
                "op": "add",
                "path": "/owners/0",
                "value": {
                    "id": team["id"],
                    "type": "team",
                },
            }
        ]
        r = requests.patch(
            f"{URL}/tables/{table_ids[critical]}",
            headers={**HEADERS, "Content-Type": "application/json-patch+json"},
            json=patch,
        )
        if r.status_code < 400:
            console.print(f"  ✓ {critical} owned by @{TEAM_NAME}")
        else:
            console.print(f"  [yellow]⚠ Owner on {critical}: {r.status_code}[/yellow]")

# ------------------------------------------------------------
# 6. Test Suite + Failing DQ Test
# ------------------------------------------------------------

console.print("\n[bold]▶ Creating test suite + a failing DQ test[/bold]")

FACT_FQN = f"{SERVICE_NAME}.{DB_NAME}.{SCHEMA_NAME}.fact_order"
SUITE_NAME = "fact_order_suite"

# post("/dataQuality/testSuites/executable", {
#     "name": SUITE_NAME,
#     "executableEntityReference": FACT_FQN,
#     "description": "Chronos demo test suite on fact_order",
# })
# console.print(f"  ✓ Test suite {SUITE_NAME}")

# 1.12 renamed /executable → /basic; field is now basicEntityReference
r = requests.post(
    f"{URL}/dataQuality/testSuites/basic",
    headers=HEADERS,
    json={
        "name": SUITE_NAME,
        "basicEntityReference": FACT_FQN,
        "description": "Chronos demo test suite on fact_order",
    },
)
if r.status_code == 409:
    console.print(f"  ✓ Test suite {SUITE_NAME} (already exists)")
elif r.status_code < 400:
    console.print(f"  ✓ Test suite {SUITE_NAME}")
else:
    # Some 1.12 builds auto-create the basic suite when a test case is added.
    # Proceed anyway.
    console.print(f"  [yellow]⚠ Suite create returned {r.status_code}, continuing (may auto-create)[/yellow]")

TEST_NAME = "fact_order_customer_id_not_null"

# post("/dataQuality/testCases", {
#     "name": TEST_NAME,
#     "entityLink": f"<#E::table::{FACT_FQN}::columns::customer_id>",
#     "testSuite": SUITE_NAME,
#     "testDefinition": "columnValuesToBeNotNull",
#     "parameterValues": [],
#     "description": "Customer ID should never be null on the orders fact table.",
# })
# console.print(f"  ✓ Test case {TEST_NAME}")

# In 1.12, test cases are auto-linked to the table's basic test suite.
# No testSuite field needed.
r = requests.post(
    f"{URL}/dataQuality/testCases",
    headers=HEADERS,
    json={
        "name": TEST_NAME,
        "entityLink": f"<#E::table::{FACT_FQN}::columns::customer_id>",
        "testDefinition": "columnValuesToBeNotNull",
        "parameterValues": [],
        "description": "Customer ID should never be null on the orders fact table.",
    },
)
if r.status_code == 409:
    console.print(f"  ✓ Test case {TEST_NAME} (already exists)")
elif r.status_code < 400:
    console.print(f"  ✓ Test case {TEST_NAME}")
else:
    console.print(f"[red]Test case create FAILED ({r.status_code}): {r.text[:500]}[/red]")
    sys.exit(1)

# test_case = get_by_fqn(
#     "dataQuality/testCases",
#     f"{FACT_FQN}.customer_id.{TEST_NAME}",
# )

# if test_case:
#     failure_time_ms = int((datetime.utcnow() - timedelta(minutes=10)).timestamp() * 1000)
#     r = requests.put(
#         f"{URL}/dataQuality/testCases/{test_case['fullyQualifiedName']}/testCaseResult",
#         headers=HEADERS,
#         json={
#             "timestamp": failure_time_ms,
#             "testCaseStatus": "Failed",
#             "result": "1247 null values found in customer_id (12% of rows)",
#             "testResultValue": [
#                 {"name": "nullCount", "value": "1247"},
#                 {"name": "nullProportion", "value": "0.12"},
#             ],
#         },
#     )
#     if r.status_code < 400:
#         console.print(f"  ✓ Failed result recorded (fired 10 min ago)")
#     else:
#         console.print(f"  [yellow]⚠ Test result push: {r.status_code} {r.text[:200]}[/yellow]")

# In 1.12 the FQN for a column-level test is {tableFQN}.{column}.{testName}
# but some builds use {tableFQN}.{testName}. Try both.
test_case = None
for candidate_fqn in (
    f"{FACT_FQN}.customer_id.{TEST_NAME}",
    f"{FACT_FQN}.{TEST_NAME}",
):
    test_case = get_by_fqn("dataQuality/testCases", candidate_fqn)
    if test_case:
        break

if test_case:
    # Give all the prior version events a clear chronological gap before the failure fires.
    # This makes the Incident Dossier timeline read naturally:
    #   events happened → short gap → failure fired.
    console.print("  [dim]Waiting 6s to establish a clean chronology before firing the failure...[/dim]")
    time.sleep(6)

    # Failure fires NOW (after all upstream events have settled).
    failure_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    tc_fqn = test_case["fullyQualifiedName"]
    r = requests.put(
        f"{URL}/dataQuality/testCases/{tc_fqn}/testCaseResult",
        headers=HEADERS,
        json={
            "timestamp": failure_time_ms,
            "testCaseStatus": "Failed",
            "result": "1247 null values found in customer_id (12% of rows)",
            "testResultValue": [
                {"name": "nullCount", "value": "1247"},
                {"name": "nullProportion", "value": "0.12"},
            ],
        },
    )
    if r.status_code < 400:
        console.print(f"  ✓ Failed result recorded (fired 10 min ago)")
    else:
        console.print(
            f"  [yellow]⚠ Test result push: {r.status_code} {r.text[:300]}[/yellow]"
        )
        console.print(f"  [dim]Will try alternate endpoint...[/dim]")
        # Some 1.12 builds use POST on a different path
        r2 = requests.post(
            f"{URL}/dataQuality/testCases/testCaseResults/{tc_fqn}",
            headers=HEADERS,
            json={
                "timestamp": failure_time_ms,
                "testCaseStatus": "Failed",
                "result": "1247 null values found in customer_id (12% of rows)",
            },
        )
        if r2.status_code < 400:
            console.print(f"  ✓ Failed result recorded (alternate endpoint)")
        else:
            console.print(
                f"  [yellow]⚠ Alternate also failed: {r2.status_code}. "
                f"Chronos can still work — it will mark the test as failed in its own logic.[/yellow]"
            )
else:
    console.print(f"  [yellow]⚠ Could not locate test case by FQN; skipping result push.[/yellow]")

# ------------------------------------------------------------
# 7. Stage the breaking change
# ------------------------------------------------------------

console.print("\n[bold]▶ Staging the incident: upstream column rename on raw_orders[/bold]")
console.print("  [dim]Pausing 3s so the 'breaking change' event is visibly AFTER other activity...[/dim]")
time.sleep(3)

raw_orders = get_by_fqn("tables", f"{SERVICE_NAME}.{DB_NAME}.{SCHEMA_NAME}.raw_orders")
patch = [
    {
        "op": "replace",
        "path": "/description",
        "value": "Raw orders dump from Shopify. BREAKING CHANGE 2026-04-20: column 'cust_id' renamed to 'customer_identifier' in upstream source. Downstream stg_orders still references the old name.",
    }
]
r = requests.patch(
    f"{URL}/tables/{raw_orders['id']}",
    headers={**HEADERS, "Content-Type": "application/json-patch+json"},
    json=patch,
)
if r.status_code < 400:
    console.print("  ✓ raw_orders description updated (version bumped)")
else:
    console.print(f"  [yellow]⚠ Description patch: {r.status_code}[/yellow]")

# ------------------------------------------------------------
# Done
# ------------------------------------------------------------

console.print(
    Panel.fit(
        "[bold green]✅ SEED COMPLETE[/bold green]\n\n"
        "[bold]What was created:[/bold]\n"
        "  • 1 service (chronos_warehouse)\n"
        "  • 1 database + 1 schema\n"
        "  • 5 tables with realistic columns\n"
        "  • 4 lineage edges\n"
        "  • Tier1 tags on fact_order + dim_customer\n"
        "  • 1 failing DQ test (fact_order.customer_id not_null)\n"
        "  • 1 staged upstream change on raw_orders\n\n"
        "[bold cyan]Verify at: http://localhost:8585/explore/tables[/bold cyan]\n"
        "[dim]Safe to re-run — script is idempotent.[/dim]",
    )
)