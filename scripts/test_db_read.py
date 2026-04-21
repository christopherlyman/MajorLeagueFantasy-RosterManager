from services.db import get_connection

sql = """
SELECT league_key, season_year, team_id, team_key, team_name, manager_name
FROM lineup_tool.team_map
WHERE league_key = '469.l.22528' AND season_year = 2026
ORDER BY team_id;
"""

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

print(f"ROWS {len(rows)}")
for row in rows:
    print(row)
