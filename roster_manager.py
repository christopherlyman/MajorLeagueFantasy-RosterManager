import os

from dash import Dash, dcc, html
from pages.batters import layout as batters_layout
from pages.pitchers import layout as pitchers_layout

app = Dash(__name__, title="MLF Roster Manager")
server = app.server

app.layout = html.Div(
    [
        html.H2("MLF Roster Manager"),
        html.Div("Container shell is running. Data wiring comes next."),
        dcc.Tabs(
            [
                dcc.Tab(label="Batters", children=batters_layout),
                dcc.Tab(label="Pitchers", children=pitchers_layout),
            ]
        ),
    ],
    style={"padding": "12px"},
)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8050")), debug=False)
