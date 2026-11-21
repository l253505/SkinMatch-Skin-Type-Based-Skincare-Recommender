# GlowGuide

The final Streamlit entry point is `app.py`. It opens the polished baby-pink interface in `glowguide_ui.py`, with product photography, wishlist, Ingredient Notebook, recommendation explanations, and ML Insights.

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The following original project files must be beside `app.py`:

- `skinmatch_recommendations.csv`
- `skincare_with_predictions.csv`
- `skincare_clean.csv`
- `ingredients_tfidf.npz`
- `model_comparison.csv`

The bundled CSV exports and ingredient matrix power the app. If required files are moved, the app displays a setup screen listing the missing files.

Keep `recommendation_engine.py`, `glowguide_ui.py`, `sync_product_photos.py`, `product_photos.json`, and the `assets` folder with the app. Trained pickle files are not needed at runtime because the app uses exported labels and calculates ingredient similarity from the saved matrix. See `GLOWGUIDE.md` for pipeline details.

## Notebook environment and checks

To run the project notebooks, install `requirements-notebooks.txt`. Run notebooks in phase order from the project directory. The app uses saved exports; launching it does not retrain models.

```powershell
python -m pip install -r requirements-notebooks.txt
python -m unittest test_recommendation_engine.py
```

Product photos are included locally. Fifteen records lack an exact verified photograph and show a neutral placeholder. Evaluation scores describe the notebook validation split, not individual product suitability. K-means grouping is explanatory context, not a quality rating.
