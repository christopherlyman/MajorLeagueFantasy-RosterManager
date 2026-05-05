import streamlit as st

batters = st.Page("pages/batters.py", title="Batters")
pitchers = st.Page("pages/pitchers.py", title="Pitchers")

pg = st.navigation([batters, pitchers])
pg.run()
