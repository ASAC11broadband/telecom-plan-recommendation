"""과정의 정합성을 검증한다. python -B -m unittest test_service_process -v

실제 API를 호출하지 않는다. 추천 정확도/사용자 만족도 시험이 아니다.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent.data import all_plans, filter_candidates, PLANS_CSV
from agent.schemas import UserProfile, ScoredPlan
from agent.agents.recommend import (recommend_node, _apply_comparison, _reference_verdict,
                                    _dedupe_identical_offers, _diverse_selection, _offer_character,
                                    _is_pareto_better, TOP_N)
from agent.agents.evaluation import _ranking_errors, evaluation_node
from agent.agents.profiling import _apply_user_age
from backend.main import app, _llm_calls, LLM_CALLS_PER_MINUTE
from backend.plans import (COMPARE_MONTHS, monthly_fee_schedule, reference_delta,
                           to_plan_item, total_cost)


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
        known = next(plan for plan in self.rows if plan['billing_price_known'])
        improved = dict(known, plan_id='improved', plan_name='개선 후보', discounted_fee=19000,
                        monthly_fee=19000, discount_period_months=None, data_gb=100)
        cheap = dict(improved, plan_id='cheap', plan_name='저용량 후보', discounted_fee=1000,
                     monthly_fee=1000, data_gb=1, data_unlimited=False)
        with patch('agent.agents.recommend.filter_candidates', return_value=[improved, cheap]):
            result = recommend_node({'profile': UserProfile(
                reference_fee_won=20000, reference_data_gb=100, user_age=30)}, {})
        self.assertEqual(result['reference_verdict']['status'], 'switch')
        by_id = {plan['plan_id']: plan for plan in result['candidates']}
        self.assertTrue(result['ranked'])
        self.assertTrue(all(_is_pareto_better(by_id[plan.plan_id], result['reference'])
                            for plan in result['ranked']))

    def test_no_better_plan_means_keep_not_unknown(self):
        """'더 나은 게 있으면'에 우위 후보가 없으면 '비교 못 함'이 아니라 '유지'다.

        비교를 한 끝에 우위가 없는 것과, 비교 자체가 성립하지 않는 것은 다르다.
        배너(유지)와 화면에 뜨는 카드(맞교환 후보)의 범위도 서로 어긋나면 안 된다.
        """
        result = recommend_node({'profile': UserProfile(
            user_age=30, reference_fee_won=20000, reference_data_gb=100.0,
            comparison_goals=['better'])}, {})
        verdict = result['reference_verdict']
        self.assertIn(verdict['status'], ('keep', 'switch'))
        self.assertTrue(result['ranked'])
        if verdict['status'] == 'keep':
            by_id = {plan['plan_id']: plan for plan in result['candidates']}
            self.assertFalse(any(_is_pareto_better(by_id[plan.plan_id], result['reference'])
                                 for plan in result['ranked']))

    def test_payback_display_prices_are_not_bills_or_deducted_twice(self):
        unknown = [plan for plan in self.rows if not plan['billing_price_known']]
        self.assertTrue(unknown)
        self.assertTrue(all(plan['billing_price_known'] for plan in filter_candidates({})))
        for row in unknown:
            item = to_plan_item(row)
            self.assertIsNone(item['totalNum'])
            self.assertIsNone(item['effectiveTotalNum'])
            self.assertEqual(item['benefitDeductible'], 0)
            self.assertIn('확인 필요', item['total'])
            self.assertEqual(_reference_verdict(row, [self.rows[0]])['status'], 'undetermined')

    def test_verified_billing_prices_come_back_as_bills_not_display_prices(self):
        """원문에서 청구액을 확인한 페이백 상품만 계산에 복귀한다.

        정정표(data/페이백_청구액_검증.csv)는 표시가에 페이백을 더해 만든 값이 아니라
        상세페이지 '월 납부액'을 그대로 옮긴 값이다. 표시가는 payback_included_fee 로
        따로 남고 청구액과 섞이지 않는다.
        """
        restored = [plan for plan in self.rows
                    if plan['billing_price_known'] and '페이백' in plan['discount_type']]
        self.assertTrue(restored)
        for row in restored:
            item = to_plan_item(row)
            self.assertIsNotNone(item['totalNum'])
            self.assertEqual(item['priceNum'], row['discounted_fee'])
            # 표시가를 청구액으로 쓰지 않는다. 둘이 같은 값이면 페이백이 없는 것이다.
            if row['payback_included_fee'] is not None:
                self.assertLessEqual(row['payback_included_fee'], row['discounted_fee'])
            # 할인 기간이 남아 있다면 실제 요금 할인이 있는 경우뿐이다.
            if row['discount_period_months'] is not None:
                self.assertLess(row['discounted_fee'], row['monthly_fee'])

        known = {plan['plan_id']: plan for plan in self.rows}
        nugget = known.get('36334')
        if nugget is not None:   # 정정표에 있는 원문 확인 사례
            self.assertEqual((nugget['discounted_fee'], nugget['payback_included_fee']), (49000, 7000))

    def test_current_plan_comparison_needs_no_product_name(self):
        """상품명 없이 현재 납부액·데이터량만 알아도 12개월 기준으로 비교한다."""
        facts = {'discounted_fee': 20000, 'data_gb': 100, 'data_unlimited': None}
        row = dict(next(plan for plan in self.rows if plan['billing_price_known']
                        and plan['discount_period_months'] is not None
                        and plan['discounted_fee'] < plan['monthly_fee']))
        delta = reference_delta(facts, row)
        self.assertEqual(delta['months'], COMPARE_MONTHS)
        self.assertEqual(delta['currentTotal'], 20000 * COMPARE_MONTHS)
        self.assertEqual(delta['candidateTotal'], total_cost(row))
        self.assertEqual(delta['totalDiff'], delta['candidateTotal'] - delta['currentTotal'])
        # 할인 종료 시점과 그 이후 가격을 함께 준다. 그래프와 총비용은 같은 목록에서 나온다.
        self.assertEqual(delta['discountEndsAfterMonths'], row['discount_period_months'])
        self.assertEqual(delta['feeAfterDiscount'], row['monthly_fee'])
        self.assertEqual(sum(point['candidate'] for point in delta['schedule']), delta['candidateTotal'])
        self.assertEqual(len(delta['schedule']), COMPARE_MONTHS)

    def test_report_gets_the_price_comparison_precomputed(self):
        """더 비싼 후보에 '더 저렴'이라고 쓰지 못하게 비교 결론을 코드가 넘긴다."""
        from agent.agents.report import _vs_current
        pricier = _vs_current({'discounted_fee': 23100, 'monthly_fee': 46640,
                               'discount_period_months': 12}, {'discounted_fee': 20000})
        self.assertEqual(pricier['결론'], '현재보다 비싸다')
        self.assertEqual(pricier['초기_월_차이'], 3100)
        cheaper = _vs_current({'discounted_fee': 15000, 'monthly_fee': 15000,
                               'discount_period_months': None}, {'discounted_fee': 20000})
        self.assertEqual(cheaper['결론'], '현재보다 싸다')
        # 현재 요금을 모르거나 청구액이 미확인이면 비교 자체를 넘기지 않는다.
        self.assertIsNone(_vs_current({'discounted_fee': 15000}, None))
        self.assertIsNone(_vs_current({'discounted_fee': 15000, 'billing_price_known': False},
                                      {'discounted_fee': 20000}))

    def test_unknown_values_are_not_counted_as_zero(self):
        """모르는 값은 0 이 아니라 None 이다. 확정 절약액이라고 부르지 않는다."""
        row = next(plan for plan in self.rows if plan['billing_price_known'])
        no_fee = reference_delta({'data_gb': 100, 'discounted_fee': None}, row)
        self.assertIsNone(no_fee['currentTotal'])
        self.assertIsNone(no_fee['totalDiff'])
        self.assertIn('현재 월 납부액', no_fee['unknowns'])

        unknown_billing = [plan for plan in self.rows if not plan['billing_price_known']]
        if unknown_billing:
            delta = reference_delta({'discounted_fee': 20000}, unknown_billing[0])
            self.assertIsNone(delta['candidateTotal'])
            self.assertIsNone(delta['totalDiff'])
        # 위약금·결합할인 손실은 언제나 미반영 항목으로 남는다.
        self.assertIn('해지 위약금', no_fee['unknowns'])
        self.assertIn('결합·가족할인 손실', no_fee['unknowns'])

    def test_monthly_schedule_matches_the_shared_total_cost(self):
        """그래프와 총비용이 같은 계산을 쓴다. 기준이 갈리면 화면끼리 숫자가 어긋난다."""
        for row in self.rows[:200]:
            self.assertEqual(sum(monthly_fee_schedule(row)), total_cost(row))

    def test_latest_preference_wins_over_the_earlier_one(self):
        """선호를 바꾸면 최신 발화가 이긴다. 대화 전체를 다시 읽어도 앞의 축이 남지 않는다."""
        from agent.agents.profiling import _repair_latest_priority
        kept = '월 30,000원 이하, 데이터 20GB 이상, 만 30세 조건은 그대로 두고, '
        price_first = kept + '가격을 가장 중요하게 봐서 다시 추천해줘.'
        data_first = kept + '데이터 제공량을 가장 중요하게 봐서 다시 추천해줘.'
        asked = '월 데이터 20GB 이상, 요금 3만원 이하로 추천해줘. 만 30세이고 혜택은 상관없어.'
        profile = UserProfile(priorities=['price'], budget_max_won=30000, min_data_gb=20)

        history = lambda *turns: chr(10).join(turns)   # 대화 전체가 한 덩어리로 들어온다
        after = _repair_latest_priority(profile, history(asked, price_first, data_first))
        self.assertEqual(after.priorities, ['data'])
        # 필수 조건은 그대로다. 바뀌는 것은 정렬 축뿐이다.
        self.assertEqual((after.budget_max_won, after.min_data_gb), (30000, 20))
        self.assertEqual(
            _repair_latest_priority(profile, history(asked, data_first, price_first)).priorities,
            ['price'])
        # 조건만 말한 발화는 정렬 요구가 아니다(_drop_inferred_priorities 와 같은 기준).
        self.assertEqual(_repair_latest_priority(profile, asked).priorities, ['price'])

    def test_weight_robustness_is_measured_not_claimed(self):
        """'가중치를 흔들어도 결론이 같다'는 발표 문장을 코드가 매번 다시 잰다.

        SMAA-2 를 '추천을 더 좋게 만드는 장치'로 설명하면 "평균 가중치와 뭐가 다르냐"는
        질문에 답할 수 없다. 대신 표본 300세트와 평균 가중치의 순위를 견준 수치를 내보낸다.
        """
        from agent.agents.recommend import TOP_N
        facts = self.client.get('/api/analysis').json()
        report = facts['weightRobustness']
        self.assertTrue(report)
        for case in report:
            self.assertEqual(case['topN'], TOP_N)
            self.assertEqual(case['samples'], facts['weightSamples'])
            self.assertGreaterEqual(case['candidates'], TOP_N)
            # 집계값은 표본 수를 넘을 수 없고, 겹침은 top_n 을 넘을 수 없다.
            self.assertLessEqual(case['identicalTopN'], case['samples'])
            self.assertLessEqual(case['sameFirst'], case['samples'])
            self.assertLessEqual(case['minOverlap'], TOP_N)

    def test_baseline_is_frozen_and_separate_from_serving_data(self):
        """기준 유도는 고정 분석본, 서비스 적용은 최신 수집본. 둘이 섞이면 안 된다.

        고정본이 최신본을 따라 움직이면 문턱을 왜 그 값으로 정했는지가 재현되지 않는다.
        """
        from agent.data import baseline_plans, BASELINE_DATE, BASELINE_PLANS_CSV
        self.assertTrue(BASELINE_PLANS_CSV.exists(), BASELINE_PLANS_CSV)
        baseline = baseline_plans()
        collected = {str(value)[:10] for value in baseline['crawled_at']}
        self.assertEqual(collected, {BASELINE_DATE})
        # 문턱을 유도한 근거가 고정본에서 그대로 재현된다.
        old_definition = int((baseline['data_unlimited'] | (baseline['qos_mbps'] >= 1.0)).sum())
        self.assertEqual((len(baseline), old_definition,
                          int(baseline['effective_unlimited'].sum())), (2759, 2007, 403))
        # 서비스가 읽는 최신본과는 다른 파일이다.
        self.assertNotEqual(BASELINE_PLANS_CSV.resolve(), PLANS_CSV.resolve())

    def test_unlimited_needs_both_allowance_and_speed(self):
        """'무제한'은 제공량과 소진 후 속도를 함께 본다.

        속도만 보던 때는 '4.5GB + 1Mbps / 100원'이 무제한 요청의 1순위였다
        (그 정의에 걸린 QoS형 1,644건의 중위 제공량이 24GB).
        """
        from agent.data import UNLIMITED_MIN_GB, UNLIMITED_QOS_MBPS, is_effectively_unlimited
        self.assertTrue(is_effectively_unlimited(False, UNLIMITED_QOS_MBPS, UNLIMITED_MIN_GB))
        self.assertFalse(is_effectively_unlimited(False, UNLIMITED_QOS_MBPS, 4.5))
        self.assertFalse(is_effectively_unlimited(False, 1.0, 150.0))
        # 제공량을 모르면 무제한으로 치지 않는다.
        self.assertFalse(is_effectively_unlimited(False, UNLIMITED_QOS_MBPS, None))
        self.assertTrue(is_effectively_unlimited(True, None, None))

        loose = filter_candidates({'data_unlimited': True})
        self.assertTrue(loose)
        for plan in loose:
            self.assertTrue(
                plan['data_unlimited']
                or (plan['data_gb'] >= UNLIMITED_MIN_GB and plan['qos_mbps'] >= UNLIMITED_QOS_MBPS),
                plan['plan_name'])
        # '완전 무제한' 요청은 기본량 무제한만 남긴다. 알뜰폰에는 그런 상품이 없어 기본 범위에서는 0건이고,
        # 통신 3사까지 넓혀야 나온다(추천 대상은 알뜰폰 - test_recommendation_scope_is_mvno).
        self.assertEqual(filter_candidates({'data_unlimited': True, 'require_full_unlimited': True}), [])
        strict = filter_candidates({'data_unlimited': True, 'require_full_unlimited': True, 'include_mno': True})
        self.assertTrue(strict)
        self.assertTrue(all(plan['data_unlimited'] for plan in strict))

    def test_current_carrier_is_not_a_search_scope(self):
        """'지금 SKT 쓰는데'의 SKT 는 출발지다. 찾는 범위로 읽으면 알뜰폰으로 옮기려는 사람에게 통신 3사만 보여 준다."""
        from agent.agents.profiling import _drop_current_carrier_scope
        misread = UserProfile(carrier_type='MNO', host_mno='SKT', reference_fee_won=69000)
        fixed = _drop_current_carrier_scope(misread, '지금 SKT 5GX 레귤러 쓰고 있는데 더 싼 요금제로 바꾸고 싶어')
        self.assertEqual((fixed.carrier_type, fixed.host_mno), (None, None))
        # 범위를 직접 말했으면 그대로 둔다.
        kept = _drop_current_carrier_scope(misread, '지금 SKT 쓰는데 SKT 안에서 더 싼 요금제 추천해줘')
        self.assertEqual((kept.carrier_type, kept.host_mno), ('MNO', 'SKT'))
        # 현재 통신사 얘기가 아니면 건드리지 않는다.
        asked = _drop_current_carrier_scope(misread, 'SKT 요금제 추천해줘')
        self.assertEqual(asked.carrier_type, 'MNO')

    def test_recommendation_scope_is_mvno(self):
        """추천 대상은 알뜰폰이다. 통신 3사는 사용자가 직접 찾을 때만 후보가 된다.

        가중치를 알뜰폰 시장 데이터로만 배웠고, 통신 3사 안에서의 변경은 결합할인·약정이 좌우하는데
        그 정보가 없다. 통신 3사 요금제는 '현재 요금제' 비교와 탐색에는 그대로 쓰인다.
        """
        from agent.data import BIG3_DIRECT_BRANDS, diagnose_empty, find_plans_by_name
        default = filter_candidates({'budget_max_won': 80000})
        self.assertTrue(default)
        for plan in default:
            self.assertEqual(plan['carrier_type'], 'MVNO', plan['plan_name'])
            self.assertNotIn(plan['mvno_brand'], BIG3_DIRECT_BRANDS, plan['plan_name'])
        # 직접 찾으면 나온다: 통신 3사 포함 / 통신 3사만 / 브랜드 지정
        widened = filter_candidates({'budget_max_won': 80000, 'include_mno': True})
        self.assertTrue(any(plan['carrier_type'] == 'MNO' for plan in widened))
        self.assertTrue(all(plan['carrier_type'] == 'MNO' for plan in filter_candidates({'carrier_type': 'MNO'})))
        # 알뜰폰에 없는 상품이면 0건으로 끝내지 않고 넓히는 길을 알려 준다.
        blockers = diagnose_empty({'data_unlimited': True, 'require_full_unlimited': True})
        self.assertTrue(any(b['field'] == 'include_mno' and b['candidates'] > 0 for b in blockers))
        # 현재 요금제로는 통신 3사 상품도 찾을 수 있어야 한다(통신 3사 -> 알뜰폰 비교).
        self.assertTrue(any(plan['carrier_type'] == 'MNO' for plan in find_plans_by_name('베이직')))

    def test_latest_unlimited_scope_wins(self):
        """'완전 무제한만' ↔ 'QoS형도 괜찮다'도 마지막에 말한 쪽이 이긴다."""
        from agent.agents.profiling import _repair_latest_unlimited_strictness as repair
        asked = '데이터 무제한인 요금제 추천해줘.'
        only_full = '완전 무제한, 속도 제한 없는 요금제만 추천해줘.'
        also_qos = '완전 무제한이 아니어도 괜찮아. 소진 후 속도가 유지되는 상품도 포함해서 추천해줘.'
        history = lambda *turns: chr(10).join(turns)

        self.assertIsNone(repair(UserProfile(data_unlimited=True), asked).require_full_unlimited)
        self.assertTrue(repair(UserProfile(data_unlimited=True),
                               history(asked, only_full)).require_full_unlimited)
        self.assertIsNone(repair(UserProfile(data_unlimited=True, require_full_unlimited=True),
                                 history(asked, only_full, also_qos)).require_full_unlimited)
        self.assertTrue(repair(UserProfile(data_unlimited=True, require_full_unlimited=True),
                               history(asked, also_qos, only_full)).require_full_unlimited)

    def test_preference_statement_is_not_a_comparison_filter(self):
        """'가격 우선'은 순위 가중치다. 현재보다 싼 것만 남기는 필수 조건이 아니다.

        실측: 선호만 바꿨는데 comparison_goals 가 cheaper 로 잡혀 후보가 0건이 됐다.
        """
        from agent.agents.profiling import _repair_general_comparison
        current = dict(reference_fee_won=20000, reference_data_gb=100.0)
        asked = '지금 월 2만원에 데이터 100GB인 요금제를 쓰고 있어. 더 나은 요금제가 있으면 추천해줘.'
        preference = '데이터 100GB 이상 조건은 그대로 두고, 가격을 가장 중요하게 봐서 다시 추천해줘.'

        after = _repair_general_comparison(
            UserProfile(**current, comparison_goals=['cheaper']), chr(10).join([asked, preference]))
        self.assertEqual(after.comparison_goals, ['better'])
        # 선호만 말한 대화에서는 비교 목표 자체가 생기지 않는다.
        self.assertIsNone(_repair_general_comparison(
            UserProfile(**current, comparison_goals=['cheaper']), preference).comparison_goals)
        # 명시적 맞교환 요청은 그대로 필수 조건으로 남는다.
        self.assertEqual(_repair_general_comparison(
            UserProfile(**current, comparison_goals=['cheaper']),
            '지금보다 더 싼 걸로 추천해줘').comparison_goals, ['cheaper'])

    def test_priority_change_keeps_hard_constraints_and_moves_the_ranking(self):
        """선호 축만 바꾼 재계산. 필수조건 필터는 그대로고 가중치는 기존 SMAA-2 보정을 쓴다."""
        from agent.mcda import evaluate_mcda, rank_smaa2
        profile = {'budget_max_won': 30000, 'min_data_gb': 20, 'user_age': 30}
        candidates = filter_candidates(profile)
        self.assertTrue(candidates)
        self.assertTrue(all(plan['discounted_fee'] <= 30000 for plan in candidates))
        self.assertTrue(all(plan['data_unlimited'] or plan['data_gb'] >= 20 for plan in candidates))

        by_price = rank_smaa2(evaluate_mcda(candidates, ['price']))
        by_data = rank_smaa2(evaluate_mcda(candidates, ['data']))
        self.assertNotEqual([plan.plan_id for plan in by_price[:5]],
                            [plan.plan_id for plan in by_data[:5]])
        # 재계산해도 후보 집합(=필수조건)은 같다. 바뀌는 것은 순서뿐이다.
        self.assertEqual({plan.plan_id for plan in by_price}, {plan.plan_id for plan in by_data})

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
        # 민감도 표는 '무제한' 두 문턱(제공량·속도)의 격자다. 한 축을 조이면 후보는
        # 줄기만 해야 하고, '현재 기준' 칸은 실제 정의가 고르는 건수와 같아야 한다.
        grid = {(r['minGb'], r['threshold']): r['count']
                for r in facts['unlimitedPolicy']['sensitivity']}
        for (min_gb, speed), count in grid.items():
            for tighter in (k for k in grid if k[0] >= min_gb and k[1] >= speed):
                self.assertLessEqual(grid[tighter], count, (tighter, (min_gb, speed)))
        # 민감도 표는 기준 유도용이라 고정 분석본으로 계산한다. 현황 통계(최신 수집본)와
        # 기준일이 다르므로 '현재 기준' 칸은 고정본의 건수와 맞아야 한다.
        from agent.data import baseline_plans, BASELINE_DATE
        self.assertEqual(facts['unlimitedPolicy']['baselineDate'], BASELINE_DATE)
        baseline = baseline_plans()
        self.assertEqual(facts['unlimitedPolicy']['baselineTotal'], len(baseline))
        current = [r for r in facts['unlimitedPolicy']['sensitivity'] if r['current']]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]['count'], int(baseline['effective_unlimited'].sum()))
        self.assertEqual((current[0]['minGb'], current[0]['threshold']),
                         (facts['unlimitedPolicy']['minGb'], facts['unlimitedPolicy']['threshold']))
        self.assertAlmostEqual(sum(r['mean'] for r in facts['weights']), 1, places=4)
        self.assertEqual(self.client.get('/api/stats').json()['dataAsOf'], facts['collectedTo'])

    def test_roaming_suspects_are_excluded_without_changing_basic_allowance(self):
        """로밍 혼입 의심 값을 국내 QoS 로 쓰지 않는 가드.

        KT 자사 무제한의 '소진 후 100/200Kbps'가 국내 속도인지 해외 로밍 속도인지
        확인되지 않아, 값을 믿지도 0 으로 쓰지도 않고 '미수집'으로 되돌린다.

        가드가 동작하는지는 **고정 분석본**으로 본다. 최신 수집본에서는 이 표기가
        아예 비어 버려(08-21 에는 180건, 09-17 에는 0건) 최신본만 보면 가드가
        살아 있는지 확인할 수 없다. 왜 사라졌는지는 원문 확인이 필요한 별개 숙제다.
        """
        from agent.data import baseline_plans
        baseline = baseline_plans()
        suspect_ids = set(
            baseline.loc[
                (baseline['carrier_type'] == 'MNO') & (baseline['host_mno'] == 'KT')
                & baseline['data_unlimited']
                & baseline['data_throttle_speed'].isin(['100Kbps', '200Kbps']),
                'plan_id',
            ]
        )
        self.assertGreater(len(suspect_ids), 0)
        # 고정본에서 의심 행의 속도는 전부 '모름'으로 지워져 있어야 한다.
        self.assertTrue(baseline.loc[baseline['plan_id'].isin(suspect_ids), 'qos_mbps'].isna().all())

        # 최신 수집본에 남아 있는 의심 행은 화면에서도 '확인 필요'로 나와야 한다.
        suspects = [r for r in self.rows if r['qos_source_suspect']]
        for row in suspects:
            self.assertTrue(row['qos_mbps'] is None and row['data_unlimited'])
            self.assertTrue(to_plan_item(row)['dataWarnings'])
            self.assertFalse(to_plan_item(row)['qosKnown'])

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

    def test_price_bases_are_two_and_each_has_one_source(self):
        """가격 기준은 둘뿐이고 각각 상수 한 곳에서만 나온다.

        순위는 '지금 내는 월 요금'(할인가), 총비용·혜택 환산은 COMPARE_MONTHS 평균이다.
        다기준 순위에서는 두 기준의 Top-5 가 같아서(질의 7종 확인) 순위를 카드에 적힌
        금액과 맞췄고, 할인 종료 후 금액은 priceRisesAfter/originalPrice 로 따로 밝힌다.
        갈아타기 판단만은 _switching_monthly_fee 로 정가 복귀를 합산한다.
        """
        from agent.data import BENEFIT_AMORTIZE_MONTHS
        from agent.mcda import _PRICE_HORIZON_MONTHS, _effective_monthly_fee, _switching_monthly_fee
        item = to_plan_item(self.rows[0])
        self.assertEqual(item['rankingMonths'], 1)
        self.assertEqual(item['compareMonths'], COMPARE_MONTHS)
        self.assertEqual(BENEFIT_AMORTIZE_MONTHS, COMPARE_MONTHS)
        self.assertEqual(_PRICE_HORIZON_MONTHS, 1)

        promo = {'discounted_fee': 1000, 'monthly_fee': 50000, 'discount_period_months': 1}
        self.assertEqual(_effective_monthly_fee(promo), 1000)           # 순위: 할인가 그대로
        self.assertEqual(_switching_monthly_fee(promo),                 # 갈아타기: 정가 복귀 포함
                         (1000 * 1 + 50000 * (COMPARE_MONTHS - 1)) / COMPARE_MONTHS)

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

    def test_top_picks_do_not_repeat_the_same_kind_of_plan(self):
        """한 사업자의 비슷한 라인업이 상위를 나눠 먹지 않는다. 다만 억지로 채우지도 않는다.

        개수는 TOP_N 을 읽는다. 숫자를 박아 두면 노출 개수를 바꿀 때마다 테스트가 깨진다.
        """
        from agent.mcda import evaluate_mcda, rank_smaa2
        profile = UserProfile(budget_max_won=30000, min_data_gb=20.0, user_age=30,
                              hard_constraints=['budget_max_won', 'min_data_gb'])
        ranking = _dedupe_identical_offers(filter_candidates(profile.model_dump()))
        by_id = {c['plan_id']: c for c in ranking}
        ordered = rank_smaa2(evaluate_mcda(ranking, profile.priorities, profile=profile))

        picked = _diverse_selection(ordered, by_id)
        self.assertEqual(len(picked), TOP_N)
        characters = [_offer_character(by_id[d.plan_id]) for d in picked]
        self.assertEqual(len(set(characters)), TOP_N, characters)
        # 다양화 없이 상위만 자르면 성격이 겹친다 — 이 테스트가 지키려는 회귀 지점이다
        self.assertLess(len({_offer_character(by_id[d.plan_id]) for d in ordered[:TOP_N]}), TOP_N)
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
        self.assertEqual([d.plan_id for d in picked], [str(i) for i in range(TOP_N)])

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
