"""Regression checks against the project's original Phase 4 notebook function."""
import ast
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
from recommendation_engine import RecommendationEngine,filter_catalog,KEY_INGREDIENTS,MIN_SIMILARITY

ROOT=Path(__file__).resolve().parent

class PhaseFourRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=RecommendationEngine(ROOT)
        notebook=json.loads((ROOT/'04_recommendation_system.ipynb').read_text(encoding='utf-8'))
        source=next(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code' and 'def recommend_products(' in ''.join(c['source']))
        tree=ast.parse(source)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='recommend_products')
        env={'pd':pd,'products':pd.read_csv(ROOT/'skinmatch_recommendations.csv'),'X_tfidf':cls.engine.matrix,'active_cols':list(KEY_INGREDIENTS),'cosine_similarity':cosine_similarity}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'original_phase_four','exec'),env)
        cls.oracle=staticmethod(env['recommend_products'])

    def test_actual_rankings_match_original_notebook(self):
        cases=[(6,'combination','All',25000),(6,'sensitive','Cleanser',6000),(3,'dry','Moisturizer',6000),(2606,'combination','Sun protection',10000),(15,'acne_prone','All',8000),(3,'oily','All',15000)]
        for reference,skin,category,budget in cases:
            with self.subTest(reference=reference,skin=skin,category=category):
                expected=self.oracle(reference,skin,category,budget)
                actual=self.engine.rank(filter_catalog(self.engine.catalog,skin,budget,category),reference).head(5)
                self.assertEqual(expected.index.tolist(),actual.index.tolist())
                if expected.empty:
                    continue
                np.testing.assert_allclose(expected.Similarity.to_numpy(),actual.Similarity.to_numpy(),atol=1e-12)
                np.testing.assert_allclose(expected.Score.to_numpy(),actual.Score.to_numpy(),atol=1e-12)
                self.assertEqual(expected.Shared_Actives.tolist(),actual.Shared_Actives.tolist())
                self.assertNotIn(reference,actual.index)

    def test_weak_matches_are_not_recommended(self):
        for reference,skin,category,budget in [(6,'combination','All',25000),(3,'dry','Moisturizer',8000),(15,'acne_prone','All',8000)]:
            with self.subTest(reference=reference,skin=skin):
                ranked=self.engine.rank(filter_catalog(self.engine.catalog,skin,budget,category),reference)
                self.assertTrue((ranked.Similarity>=MIN_SIMILARITY).all())
                self.assertTrue(ranked.Score.is_monotonic_decreasing)

    def test_sunscreen_results_exclude_self_tanners(self):
        sun=filter_catalog(self.engine.catalog,'combination',60000,'Sun protection')
        self.assertFalse(sun['name'].str.contains('tan|bronz',case=False).any())
        self.assertFalse(sun.empty)

    def test_original_labels_take_precedence_over_predictions(self):
        df=self.engine.catalog
        labeled=pd.to_numeric(df.has_skin_label).eq(1)
        for skin in ['dry','oily','combination','normal','sensitive','acne_prone']:
            expected=pd.to_numeric(df['tag_'+skin],errors='coerce').fillna(0).astype(int)
            expected.loc[~labeled]=pd.to_numeric(df.loc[~labeled,'pred_tag_'+skin],errors='raise').astype(int)
            np.testing.assert_array_equal(expected.to_numpy(),df['match_tag_'+skin].astype(int).to_numpy())

    def test_explanation_reports_actual_shared_ingredients(self):
        candidates=filter_catalog(self.engine.catalog,'Combination',25000)
        row=self.engine.rank(candidates,6).iloc[0]
        explanation=self.engine.explain(row,6,'Combination')
        self.assertEqual(len(explanation['shared_actives']),int(row.Shared_Actives))
        self.assertEqual(set(explanation['common_ingredients']),set(row.ingredients_clean.split('|'))&set(self.engine.catalog.loc[6,'ingredients_clean'].split('|')))
        self.assertEqual(explanation['same_cluster'],row.Cluster==self.engine.catalog.loc[6,'Cluster'])

if __name__=='__main__':unittest.main()
