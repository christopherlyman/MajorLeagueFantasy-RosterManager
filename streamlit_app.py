import streamlit as st

batters = st.Page("views/batters.py", title="Batters", default=True)
pitchers = st.Page("views/pitchers.py", title="Pitchers")
evaluation = st.Page("views/evaluation.py", title="Evaluation")

pg = st.navigation([batters, pitchers, evaluation])
pg.run()
