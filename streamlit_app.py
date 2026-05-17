import streamlit as st

batters = st.Page("views/batters.py", title="Batters", default=True)
pitchers = st.Page("views/pitchers.py", title="Pitchers")

pg = st.navigation([batters, pitchers])
pg.run()
