"""Start GlowGuide: python -m streamlit run app.py"""
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
REQUIRED_DATA = (
    "skinmatch_recommendations.csv",
    "skincare_with_predictions.csv",
    "skincare_clean.csv",
    "ingredients_tfidf.npz",
    "model_comparison.csv",
)
missing = [name for name in REQUIRED_DATA if not (ROOT / name).is_file()]

if missing:
    st.set_page_config(page_title="GlowGuide · Project setup", page_icon="🌷", layout="wide")
    st.markdown("<style>.stApp{background:#fffafb;color:#522b44}</style>", unsafe_allow_html=True)
    st.title("GlowGuide 🌷")
    st.error("The project data files are missing from the app folder.")
    st.write("Copy these original project files beside app.py, then rerun the app:")
    st.code("\n".join(missing), language=None)
    st.caption(f"Project folder: {ROOT}")
    st.stop()

from glowguide_ui import render_app

render_app()
