from dash import html
import dash_ag_grid as dag

from services.queries import (
    fetch_available_batter_rows,
    fetch_batter_roster_rows,
    get_default_context,
)
from services.scoring import START_WORTHY_THRESHOLD

ctx = get_default_context()

roster_rows = []
available_rows = []
message = ""

if ctx["league_key"] and ctx["team_key"] and ctx["as_of_date"]:
    roster_rows = fetch_batter_roster_rows(
        league_key=ctx["league_key"],
        team_key=ctx["team_key"],
        as_of_date=ctx["as_of_date"],
    )
    available_rows = fetch_available_batter_rows(
        league_key=ctx["league_key"],
        team_key=ctx["team_key"],
        as_of_date=ctx["as_of_date"],
    )
    message = (
        f'League={ctx["league_key"]} | Team={ctx["team_key"]} | '
        f'Date={ctx["as_of_date"]} | My Batters={len(roster_rows)} | '
        f'Available={len(available_rows)}'
    )
else:
    message = "Missing DEFAULT_LEAGUE_KEY / DEFAULT_TEAM_KEY in .env"

roster_cols = [
    {"headerName": "Slot", "field": "slot_display", "width": 85},
    {"headerName": "Player (MLB)", "field": "player_display", "flex": 1.6, "minWidth": 220},
    {"headerName": "Game", "field": "game_display", "flex": 1.1, "minWidth": 170},
    {"headerName": "Eligible", "field": "eligible_display", "flex": 0.9, "minWidth": 120},
    {"headerName": "Status", "field": "status_display", "width": 95},
    {"headerName": "Ranking", "field": "ranking", "width": 90},
    {"headerName": "Band", "field": "ranking_band", "width": 110},
    {"headerName": "Rank Reason", "field": "note_short", "flex": 1.1, "minWidth": 180},
]

available_cols = [
    {"headerName": "Player (MLB)", "field": "player_display", "flex": 1.8, "minWidth": 250},
    {"headerName": "Eligible", "field": "eligible_display", "flex": 1.2, "minWidth": 150},
    {"headerName": "Ranking", "field": "ranking", "width": 95},
    {"headerName": "Delta", "field": "comparison_delta", "width": 90},
]

layout = html.Div(
    [
        html.H3("Batters"),
        html.Div(message),
        html.Div(
            f"Usual Suspects provisional start-worthy threshold: {int(START_WORTHY_THRESHOLD)}+.",
            style={"marginTop": "6px", "marginBottom": "8px"},
        ),
        html.Br(),
        html.H4("My Batters for Today"),
        html.Div(
            "Slots always display in lineup order, then BN, IL, and NA.",
            style={"marginBottom": "8px"},
        ),
        dag.AgGrid(
            id="batters-roster-grid",
            columnDefs=roster_cols,
            rowData=roster_rows,
            defaultColDef={"sortable": True, "filter": True, "resizable": True},
            dashGridOptions={"animateRows": False, "rowSelection": "single"},
            style={"height": "700px", "width": "100%"},
        ),
        html.Br(),
        html.H4("Top Available Free Agents"),
        html.Div(
            "Placeholder: free-agent query not wired yet.",
            style={"marginBottom": "8px"},
        ),
        dag.AgGrid(
            id="batters-available-grid",
            columnDefs=available_cols,
            rowData=available_rows,
            defaultColDef={"sortable": True, "filter": True, "resizable": True},
            dashGridOptions={"animateRows": False, "rowSelection": "single"},
            style={"height": "320px", "width": "100%"},
        ),
    ],
    style={"padding": "12px"},
)
