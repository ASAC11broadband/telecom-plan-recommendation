"""과정의 정합성을 검증한다. python -B -m unittest test_service_process -v

실제 API를 호출하지 않는다. 추천 정확도/사용자 만족도 시험이 아니다.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent.data import all_plans, filter_candidates
from agent.schemas import UserProfile, ScoredPlan, Evaluation
from agent.agents.recommend import (recommend_node, _apply_comparison, _reference_verdict,
                                    _dedupe_identical_offers, _diverse_selection, _offer_character)
from agent.agents.evaluation import _ranking_errors, evaluation_node
from agent.agents.profiling import _apply_user_age
from backend.main import app, _llm_calls, LLM_CALLS_PER_MINUTE
from backend.analysis import analysis_snapshot
from backend.plans import COMPARE_MONTHS, to_plan_item, total_cost


class ServiceProcessTests(unittest.TestCase):
    def test_current_spec_is_not_a_catalog_product_name(self):
        from agent.data import find_plans_mentioned_in_text
        from agent.agents.profiling import _repair_reference_plan_name, _repair_general_comparison
        query = '지금 월 2만원에 데이터 100GB인 요금제를 쓰고 있어. 만 30세야. 현재 요금제보다 유리한 요금제가 있으면 추천해줘.'
        self.assertEqual(find_plans_mentioned_in_text(query), [])
        profile = UserProfile(reference_plan_name='데이터100G(밀리의서재)+',
                              reference_fee_won=20000, reference_data_gb=100,
                              comparison_goals=['cheaper'])
        self.assertIsNone(_repair_reference_plan_name(profile, query).reference_plan_name)
        self.assertEqual(_repair_general_comparison(profile, query).comparison_goals, ['better'])

    def test_current_plan_comparison_shows_the_improving_candidates(self):
        from agent.agents.recommend import _is_pareto_better
        result = recommend_node({'profile': UserProfile(
            reference_fee_won=20000, reference_data_gb=100, user_age=30)}, {})
        self.assertEqual(result['reference_verdict']['status'], 'switch')
        by_id = {plan['plan_id']: plan for plan in result['candidates']}
        self.assertTrue(result['ranked'])
        self.assertTrue(all(_is_pareto_better(by_id[plan.plan_id], result['reference'])
                            for plan in result['ranked']))

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

    def test_keep_current_plan_is_distinguished_from_cannot_tell(self):
        """유지 권고와 판단 불가는 다른 상태다. 후보가 없다는 사실만으로 유지가 유리하다고 하지 않는다."""
        current = {'discounted_fee': 30000, 'data_gb': 50.0, 'data_unlimited': False}

        # 요금만 알고 데이터를 모르면 비교가 성립하지 않는다
        fee_only = _reference_verdict({'discounted_fee': 30000}, [{'discounted_fee': 10000, 'data_gb': 5.0}])
        self.assertEqual(fee_only['status'], 'undetermined')
        self.assertIn('데이터 제공량', fee_only['missing'])

        # 후보 0건은 '유지가 유리'가 아니라 '판단 불가'
        empty = _reference_verdict(current, [])
        self.assertEqual(empty['status'], 'undetermined')
        self.assertIn('유리하다는 뜻은 아닙니다', empty['reason'])

        # 모든 항목에서 나쁘지 않고 한 항목이 나은 후보가 있으면 전환
        better = _reference_verdict(current, [{'discounted_fee': 20000, 'data_gb': 50.0}])
        self.assertEqual(better['status'], 'switch')
        self.assertEqual(better['betterCount'], 1)

        # 더 싸지만 데이터가 적은 후보뿐이면 유지. 맞교환이라는 사실을 함께 말한다.
        tradeoff = _reference_verdict(current, [{'discounted_fee': 9000, 'data_gb': 5.0}])
        self.assertEqual(tradeoff['status'], 'keep')
        self.assertEqual(tradeoff['cheaperCount'], 1)
        self.assertIn('맞교환', tradeoff['reason'])

        # 어느 상태든 데이터로 알 수 없는 항목은 확인 안내로 남는다
        for verdict in (fee_only, empty, better, tradeoff):
            self.assertTrue(any('결합할인' in note for note in verdict['confirm']))
        self.assertIsNone(_reference_verdict(None, []))

    def test_current_fee_is_not_turned_into_a_budget_cap(self):
        """'지금 월 3만원인데'는 현재 납부액이지 예산 상한이 아니다."""
        from agent.agents.profiling import _apply_reference_fee
        asked = '지금 쓰는 요금제가 월 3만원인데 바꾸는 게 나을까?'
        fixed = _apply_reference_fee(
            UserProfile(budget_max_won=30000, hard_constraints=['budget_max_won']), asked)
        self.assertEqual(fixed.reference_fee_won, 30000)
        self.assertIsNone(fixed.budget_max_won)
        self.assertNotIn('budget_max_won', fixed.hard_constraints)

    def test_recurring_cash_without_duration_is_not_deductible(self):
        from agent.data import benefit_summary
        for basis in ('monthly', ''):
            value = benefit_summary([{'name': '매달 5천원 페이백', 'value_won': 5000,
                                      'value_basis': basis, 'categories': ['사은품/페이백']}])
            self.assertTrue(value['estimated'])
            self.assertEqual(value['deductible_won'], 0)
        value = benefit_summary([{'name': '3개월 유지 후 일시금', 'value_won': 24000,
                                  'value_basis': 'one_off', 'months': 3, 'categories': ['사은품/페이백']}])
        self.assertEqual(value['monthly_won'], 2000)

    def test_reference_verdict_banners_are_returned_by_api(self):
        cases = [
            ({'discounted_fee': 20000, 'data_gb': 100}, [{'discounted_fee': 15000, 'data_gb': 100}], 'switch'),
            ({'discounted_fee': 5000, 'data_unlimited': True}, [{'discounted_fee': 10000, 'data_gb': 100}], 'keep'),
            ({'discounted_fee': 20000, 'data_unlimited': False}, [{'discounted_fee': 15000, 'data_gb': 100}], 'undetermined'),
        ]
        for reference, candidates, expected in cases:
            with self.subTest(expected=expected):
                state = {'profile': UserProfile(), 'reference': reference, 'candidates': [], 'ranked': [],
                         'reference_verdict': _reference_verdict(reference, candidates)}
                with patch('backend.main.graph.invoke', return_value=state):
                    response = self.client.post('/api/recommend', json={'messages': [{'role': 'user', 'content': '현재 요금제 비교'}]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['referenceVerdict']['status'], expected)

    def test_reference_comparison_uses_full_period_not_intro_price(self):
        current = {'discounted_fee': 20000, 'data_gb': 50}
        promo = {'discounted_fee': 1000, 'monthly_fee': 50000,
                 'discount_period_months': 1, 'data_gb': 50}
        self.assertEqual(_reference_verdict(current, [promo])['status'], 'keep')
        self.assertEqual(_apply_comparison([promo], current, ['cheaper']), [])
        self.assertEqual(_reference_verdict(current, [dict(promo, discount_period_months=None)])['status'], 'undetermined')

    def test_false_unlimited_is_not_known_allowance(self):
        incomplete = {'discounted_fee': 20000, 'data_unlimited': False, 'data_gb': None}
        known = {'discounted_fee': 10000, 'data_gb': 50}
        self.assertEqual(_reference_verdict(incomplete, [known])['status'], 'undetermined')
        self.assertEqual(_reference_verdict(known, [incomplete])['status'], 'undetermined')

    def test_named_current_plan_uses_reported_bill_without_mutating_catalog(self):
        from agent.agents.recommend import _resolve_reference
        catalog = {'plan_id': 'current', 'plan_name': '현재상품', 'discounted_fee': 50000,
                   'monthly_fee': 70000, 'discount_period_months': 3,
                   'data_gb': 100, 'data_unlimited': True}
        with patch('agent.agents.recommend.find_plans_by_name', return_value=[catalog]):
            reference, question = _resolve_reference(UserProfile(
                reference_plan_name='현재상품', reference_fee_won=15000, reference_data_gb=20))
        self.assertIsNone(question)
        self.assertEqual(reference['discounted_fee'], 15000)
        self.assertEqual(reference['monthly_fee'], 15000)
        self.assertIsNone(reference['discount_period_months'])
        self.assertFalse(reference['data_unlimited'])
        self.assertEqual(catalog['discounted_fee'], 50000)

    def test_recommend_exposes_reference_verdict_through_api(self):
        state = {'profile': UserProfile(reference_fee_won=50000),
                 'reference': {'discounted_fee': 50000},
                 'reference_verdict': _reference_verdict({'discounted_fee': 50000}, []),
                 'ranked': [], 'candidates': []}
        with patch('backend.main.graph.invoke', return_value=state):
            response = self.client.post('/api/recommend', json={'messages': [{'role': 'user', 'content': '현재 5만원'}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['referenceVerdict']['status'], 'undetermined')

    def test_same_name_different_offer_is_not_deleted(self):
        """이름이 같아도 가입 조건·혜택이 다르면 별개 상품이다. 완전히 같은 행만 합친다."""
        base = {'plan_name': '초이스90', 'carrier': 'KT', 'carrier_type': 'MNO', 'host_mno': 'KT',
                'mvno_brand': '', 'network_gen': '5G', 'data_gb': 90.0, 'data_unlimited': False,
                'voice_minutes': None, 'voice_unlimited': True, 'included_benefits': ['A'],
                'age_condition': '', 'discounted_fee': 90000}
        youth = {**base, 'age_condition': '만 34세 이하', 'included_benefits': ['A', 'B']}
        same_but_pricier = {**base, 'discounted_fee': 95000}
        kept = _dedupe_identical_offers([base, youth, same_but_pricier])
        self.assertEqual(len(kept), 2, [p['age_condition'] for p in kept])
        self.assertEqual(sorted(p['discounted_fee'] for p in kept), [90000, 90000])

        # 실데이터: 예전 이름 기준 묶기보다 살아남는 행이 많아야 한다
        names_only = len({r['plan_name'] for r in self.rows})
        self.assertGreater(len(_dedupe_identical_offers(self.rows)), names_only)

    def test_top5_does_not_repeat_the_same_kind_of_plan(self):
        """한 사업자의 비슷한 라인업이 상위를 나눠 먹지 않는다. 다만 억지로 채우지도 않는다."""
        from agent.mcda import evaluate_mcda, rank_smaa2
        profile = UserProfile(budget_max_won=30000, min_data_gb=20.0, user_age=30,
                              hard_constraints=['budget_max_won', 'min_data_gb'])
        ranking = _dedupe_identical_offers(filter_candidates(profile.model_dump()))
        by_id = {c['plan_id']: c for c in ranking}
        ordered = rank_smaa2(evaluate_mcda(ranking, profile.priorities, profile=profile))

        picked = _diverse_selection(ordered, by_id)
        self.assertEqual(len(picked), 5)
        characters = [_offer_character(by_id[d.plan_id]) for d in picked]
        self.assertEqual(len(set(characters)), 5, characters)
        # 변경 전(단순 상위 5)은 성격이 겹쳤다 — 이 테스트가 지키려는 회귀 지점이다
        self.assertLess(len({_offer_character(by_id[d.plan_id]) for d in ordered[:5]}), 5)
        # 순위 정합성과 중복 추천 검증을 그대로 통과해야 한다
        ranks = [d.smaa2_expected_rank for d in picked]
        self.assertEqual(ranks, sorted(ranks))
        names = [by_id[d.plan_id]['plan_name'] for d in picked]
        self.assertEqual(len(names), len(set(names)))

    def test_diversity_never_invents_choices_it_does_not_have(self):
        """성격이 겹치는 후보뿐이면 칸을 억지로 만들지 않고 기대순위대로 채운다."""
        from agent.mcda import MCDAResult
        same = {'carrier': 'A', 'data_gb': 20.0, 'data_unlimited': False, 'data_tier': 'capped'}
        by_id = {str(i): {**same, 'plan_id': str(i), 'plan_name': f'요금제{i}'} for i in range(7)}
        ordered = [MCDAResult(plan_id=str(i), smaa2_first_rank_acceptability=0.0,
                              smaa2_expected_rank=float(i + 1), smaa2_score=100 - i,
                              favorable_weights=()) for i in range(7)]
        picked = _diverse_selection(ordered, by_id)
        self.assertEqual([d.plan_id for d in picked], ['0', '1', '2', '3', '4'])

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
