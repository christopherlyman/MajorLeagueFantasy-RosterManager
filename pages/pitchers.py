from dash import html

layout = html.Div(
    [
        html.H3("Pitchers"),
        html.Div("Left: roster | Middle: today's pitchers | Right: top available pitchers"),
    ],
    style={"padding": "12px"},
)
