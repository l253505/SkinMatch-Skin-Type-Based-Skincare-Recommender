# GlowGuide

GlowGuide preserves the rose-and-plum design while making the project's data science visible and reproducible.

## Run

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Features

- Reference-product recommendations with live cosine similarity, shared key ingredients, common listed ingredients, skin-label provenance, and K-means group context.
- Collection browsing with skin type, brand, category, ingredient preferences, search, and PKR budget filters.
- Ingredient search as paginated product cards with highlighted matches and expandable full formulas.
- Session wishlist with PKR totals and CSV export.
- An explanation of the four project phases and an interactive ingredient-group inspector.

## Recommendation pipeline

`recommendation_engine.py` loads 2,724 records and the saved 2,724 by 7,316 TF-IDF matrix. It verifies row identity against `skincare_clean.csv` before ranking.

Eligible products must match the selected skin type, budget, and filters. Phase 4 excludes the reference itself, counts shared flags among six tracked key ingredients, computes TF-IDF cosine similarity, removes zero-overlap candidates, and sorts by shared-flag count first and cosine similarity second. Displayed percentages are calculated from the matrix.

The app uses original labels for 1,552 records and saved KNN-inferred labels for 1,162 records. Nine exported K-means groups describe 11 binary ingredient features. Group membership is explanatory context and does not change Phase 4 ranking. KNN and K-means training remains in the notebooks; the app uses their saved outputs.

Ingredient presence does not establish concentration. Cosine similarity is not a probability of safety, efficacy, or skin suitability. PKR prices are dataset estimates rather than live retail quotes.

## Product photographs

The registry contains 2,697 unique sourced photo matches covering 2,724 of 2,724 product records. Images are bundled under `assets/products/`; each photographed card links to its source. Sources include original brands, retailers, and ingredient catalogs. Matching checks meaningful variants, strengths, SPF, and product form; reviewed identity aliases are recorded in `product_photo_name_aliases.json`.

All 2,724 records now have product imagery. For 2,723 records, the product or documented contents are verified. The Algenist Dark Circle Corrector Set uses a gallery of the three products listed in the brand’s archived Dark Circle Correcting Kit. Its exact dataset edition is unconfirmed, visibly labelled, and excluded from Verified photos only. See products_needing_photo_verification.csv. Product sizes and packaging editions can differ.

`resolve_product_photo_search.py` validates saved source candidates and decodes downloaded files before adding them. Known placeholder hashes are rejected. Candidate evidence, reviewed aliases, and replacement audits remain for traceability.

The rose hero and Ingredient Notebook editorial assets are decorative AI-created imagery, separate from branded product photography. Prompts are stored in `assets/image-prompts.json`.

## Validation

```powershell
python -m unittest test_recommendation_engine.py
```

Regression checks execute the original Phase 4 notebook function as an independent oracle and compare ranking, shared flags, and similarity across skin types, categories, and budgets. Additional assertions verify label precedence and ingredient explanations.

## Runtime files

Include `app.py`, `glowguide_ui.py`, `recommendation_engine.py`, `sync_product_photos.py`, `requirements.txt`, `.streamlit/config.toml`, `skinmatch_recommendations.csv`, `skincare_clean.csv`, `ingredients_tfidf.npz`, `product_photos.json`, and `assets/`. Other notebooks and model files document training; the app does not retrain models at runtime.
