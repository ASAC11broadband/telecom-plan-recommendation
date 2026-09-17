"""과정의 정합성을 검증한다. python -B -m unittest test_service_process -v

실제 API를 호출하지 않는다. 추천 정확도/사용자 만족도 시험이 아니다.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent.data import all_plans, filter_candidates
from agent.schemas import UserProfile, ScoredPlan, Evaluation
from agent.agents.recommend import recommend_node, _apply_comparison
from agent.agents.evaluation import _ranking_errors, evaluation_node
from agent.agents.profiling import _apply_user_age
from backend.main import app, _llm_calls, LLM_CALLS_PER_MINUTE
from backend.analysis import analysis_snapshot
from backend.plans import COMPARE_MONTHS, to_plan_item, total_cost


class ServiceProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.rows = all_plans()

    def tearDown(self):
        _llm_calls.clear()

    def test_eda_matches_runtime_rows_and_missing_is_not_zero(self):
        facts = self.client.get('/api/analysis').json()
        self.assertEqual(facts['total'], len(self.rows))
        self.assertEqual(sum(r['count'] for r in facts['carriers']), len(self.rows))
        self.assertEqual(sum(r['count'] for r in facts['tiers']), len(self.rows))
        counts = [r['count'] for r in facts['unlimitedPolicy']['sensitivity']]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertAlmostEqual(sum(r['mean'] for r in facts['weights']), 1, places=4)
        self.assertEqual(self.client.get('/api/stats').json()['dataAsOf'], facts['collectedTo'])

    def test_roaming_suspects_are_excluded_without_changing_basic_allowance(self):
        suspects = [r for r in self.rows if r['qos_source_suspect']]
        self.assertGreater(len(suspects), 0)
        self.assertTrue(all(r['qos_mbps'] is None and r['data_unlimited'] for r in suspects))
        self.assertTrue(to_plan_item(suspects[0])['dataWarnings'])
        self.assertFalse(to_plan_item(suspects[0])['qosKnown'])

    def test_unlimited_policy_and_explicit_constraints(self):
        rows = filter_candidates({'data_unlimited': True, 'budget_max_won': 30000})
        self.assertTrue(rows)
        self.assertTrue(all(r['discounted_fee'] <= 30000 and (r['data_unlimited'] or r['qos_mbps'] >= 1) for r in rows))
        strict = filter_candidates({'data_unlimited': True, 'require_full_unlimited': True})
        self.assertTrue(all(r['data_unlimited'] for r in strict))

    def test_age_band_does_not_invent_eligibility(self):
        self.assertIsNone(_apply_user_age(UserProfile(user_age=25), '20대입니다').user_age)
        self.assertEqual(_apply_user_age(UserProfile(user_age=25), '아니 만 35세예요').user_age, 35)

    def test_recommendation_exposes_actual_rank_and_preserves_hard_conditions(self):
        profile = UserProfile(budget_max_won=30000, min_data_gb=20, hard_constraints=['budget_max_won', 'min_data_gb'])
        state = recommend_node({'profile': profile}, {})
        self.assertEqual(state['recommendation_trace']['eligibleCount'], len(state['candidates']))
        ranked = state['ranked']
        self.assertTrue(ranked)
        self.assertEqual([r.expected_rank for r in ranked], sorted(r.expected_rank for r in ranked))
        self.assertTrue(all(0 <= r.first_rank_acceptability <= 1 for r in ranked))
        self.assertTrue(all(r['discounted_fee'] <= 30000 for r in state['candidates']))

    def test_multi_criteria_rank_is_not_rejected_by_single_axis(self):
        profile = UserProfile(priorities=['price'])
        ranked = [ScoredPlan(plan_id='a', plan_name='A', score=95, expected_rank=1.1),
                  ScoredPlan(plan_id='b', plan_name='B', score=90, expected_rank=1.9)]
        rows = [{'discounted_fee': 20000}, {'discounted_fee': 10000}]
        self.assertEqual(_ranking_errors(profile, ranked, rows), [])
        ranked[0].expected_rank = 2.0
        self.assertTrue(_ranking_errors(profile, ranked, rows))

    def test_unknown_discount_is_labeled_as_assumption(self):
        row = {**self.rows[0], 'discounted_fee': 1000, 'monthly_fee': 20000, 'discount_period_months': None}
        item = to_plan_item(row)
        self.assertTrue(item['costIsEstimate'])
        self.assertIn('확인 필요', item['priceNote'])
        self.assertEqual(item['rankingAverageFee'], 1000)
        self.assertEqual(total_cost({**row, 'discount_period_months': 2}), 1000 * 2 + 20000 * (COMPARE_MONTHS - 2))

    def test_compare_period_is_single_source_of_truth(self):
        """추천 가격 평가와 화면 총비용이 같은 기간을 써야 서로 비교된다."""
        from agent.data import BENEFIT_AMORTIZE_MONTHS
        item = to_plan_item(self.rows[0])
        self.assertEqual(item['compareMonths'], item['rankingMonths'])
        self.assertEqual(BENEFIT_AMORTIZE_MONTHS, COMPARE_MONTHS)

    def test_benefit_is_not_deducted_without_confirmed_usage(self):
        """이용 여부·지급 조건이 확인되지 않은 혜택은 납부액에서 빼지 않는다."""
        subscription = next(
            r for r in self.rows
            if r['benefit_value_won'] and not r['benefit_deductible_won']
        )
        item = to_plan_item(subscription)
        self.assertGreater(item['benefitValue'], 0)
        self.assertEqual(item['benefitDeductible'], 0)
        self.assertEqual(item['effectiveTotalNum'], item['totalNum'])

    def test_benefit_axis_is_neutral_when_not_requested(self):
        """'혜택은 상관없어' 사용자에게 혜택 금액·개수가 순위를 바꾸면 안 된다."""
        from agent.mcda import CRITERIA, _discriminating, _utility_rows
        rows = [r for r in self.rows if r['benefit_value_won']][:20]
        rows += [r for r in self.rows if not r['benefit_value_won']][:20]
        utilities = _utility_rows(rows, UserProfile(budget_max_won=50000))
        self.assertNotIn('benefit', _discriminating(utilities))
        index = CRITERIA.index('benefit')
        self.assertEqual({round(row[index], 6) for row in utilities}, {0.5})

    def test_preferred_benefit_does_not_remove_candidates(self):
        """선호로 말한 혜택은 필터가 아니다 (알뜰폰＋별도 구독 비교가 가능해야 한다)."""
        hard = filter_candidates({
            'budget_max_won': 50000, 'wanted_benefits': ['넷플릭스'],
            'hard_constraints': ['budget_max_won', 'wanted_benefits'],
        })
        soft = filter_candidates({
            'budget_max_won': 50000, 'wanted_benefits': ['넷플릭스'],
            'hard_constraints': ['budget_max_won'],
        })
        self.assertGreater(len(soft), len(hard))
        self.assertTrue(any(r['carrier_type'] == 'MVNO' for r in soft))

    def test_manual_current_plan_no_longer_crashes_api(self):
        state = {'profile': UserProfile(reference_fee_won=50000), 'reference': {'discounted_fee': 50000},
                 'ranked': [], 'candidates': []}
        with patch('backend.main.graph.invoke', return_value=state):
            response = self.client.post('/api/recommend', json={'messages': [{'role': 'user', 'content': '현재 5만원'}]})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['referencePlan'])
        self.assertEqual(response.json()['referenceFacts']['discounted_fee'], 50000)

    def test_report_only_errors_retry_report(self):
        row = self.rows[0]
        state = {'ranked': [ScoredPlan(plan_id=row['plan_id'], plan_name=row['plan_name'], score=90)],
                 'candidates': [row], 'report': '', 'profile': None}
        result = evaluation_node(state, {})
        self.assertFalse(result['evaluation'].passed)
        self.assertEqual(result['evaluation'].retry_target, 'report')

    def test_input_limits(self):
        self.assertEqual(self.client.post('/api/analysis/ask', json={'question': 'x' * 2001}).status_code, 422)
        self.assertEqual(self.client.post('/api/recommend', json={'messages': []}).status_code, 422)

    def test_all_ai_endpoints_share_budget_guard(self):
        import time
        _llm_calls.extend([time.monotonic()] * LLM_CALLS_PER_MINUTE)
        self.assertEqual(self.client.post('/api/ask', json={'planId': self.rows[0]['plan_id'], 'question': '가격?'}).status_code, 429)
        self.assertEqual(self.client.post('/api/analysis/ask', json={'question': '설명'}).status_code, 429)

    def test_ai_failure_does_not_leak_exception_content(self):
        with patch('backend.main.graph.invoke', side_effect=RuntimeError('secret-test-token')):
            response = self.client.post('/api/recommend', json={'messages': [{'role': 'user', 'content': '3만원'}]})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-test-token', response.text)


if __name__ == '__main__':
    unittest.main()
