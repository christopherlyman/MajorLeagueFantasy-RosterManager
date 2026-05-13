import os
import streamlit as st

APP_DISPLAY_NAME = os.getenv("APP_DISPLAY_NAME", "MLF Roster Manager")

st.set_page_config(page_title=f"{APP_DISPLAY_NAME} - Pitchers", layout="wide")

st.title("Pitchers")

st.info(
    "Pitcher UI is the next phase. "
    "This page will hold pitcher roster, daily pitcher decisions, and pitcher free-agent workflow."
)
