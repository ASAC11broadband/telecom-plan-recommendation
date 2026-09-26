"""과정의 정합성을 검증한다. python -B -m unittest test_service_process -v

실제 API를 호출하지 않는다. 추천 정확도/사용자 만족도 시험이 아니다.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent.data import all_plans, filter_candidates, diagnose_empty, PLANS_CSV, CONSTRAINT_LABELS
from agent.schemas import UserProfile, ScoredPlan
from agent.agents.recommend import (recommend_node, _apply_comparison, _reference_verdict,
                                    _dedupe_identical_offers, _diverse_selection, _offer_character,
                                    _is_pareto_better, _resolve_reference,
                                    _same_or_equivalent_to_reference, TOP_N)
from agent.agents.evaluation import _ranking_errors, evaluation_node
from agent.agents.profiling import (BENEFIT_PREFERENCE_QUESTION, benefit_preference_missing,
                                    _apply_daily_allowance, _apply_explicit_qos_min, _apply_relaxed_fields,
                                    _apply_usage_based_qos,
                                    _apply_user_age, _drop_reference_name_data_constraint,
                                    _repair_budget_bounds, _repair_general_comparison,
                                    _drop_unrequested_benefit_followup)
from backend.main import (app, Message, _quick_chat_response, _inferred_relaxed_fields,
                          _llm_calls, LLM_CALLS_PER_MINUTE)
from backend.plans import (COMPARE_MONTHS, monthly_fee_schedule, reference_delta,
                           to_plan_item, total_cost)
from agent.usage import (AVERAGE_MOBILE_GAME_GB_PER_HOUR, SPECIFIC_GAME_GB_PER_HOUR,
                         estimate_monthly_data_gb)


class ServiceProcessTests(unittest.TestCase):
    def test_implicit_one_in_korean_money_units_is_recognized(self):
        implicit_man = _repair_budget_bounds(UserProfile(), '월 요금이 만원 이하인 요금제')
        explicit_thousand = _repair_budget_bounds(UserProfile(), '월 5천원 이하인 요금제')
        implicit_thousand = _repair_budget_bounds(UserProfile(), '월 천원 이하인 요금제')

        self.assertEqual(implicit_man.budget_max_won, 10_000)
        self.assertEqual(explicit_thousand.budget_max_won, 5_000)
        self.assertEqual(implicit_thousand.budget_max_won, 1_000)

    def test_full_benefit_suffix_wins_when_current_plan_is_mentioned(self):
        """괄호 속 제휴명까지 말했으면 같은 본체의 다른 혜택 상품으로 바꾸지 않는다."""
        from agent.data import find_plans_mentioned_in_text
        from agent.agents.profiling import _repair_reference_plan_name

        query = '현재 요금제가 TOP 11GB 기본 (CU할인) 이건데 이것보다 데이터 많은 걸 추천해줘'
        mentioned = find_plans_mentioned_in_text(query)
        self.assertTrue(mentioned)
        self.assertEqual({plan['plan_name'] for plan in mentioned}, {'TOP 11GB 기본 (CU할인)'})

        repaired = _repair_reference_plan_name(
            UserProfile(reference_plan_name='TOP 11GB 기본'), query)
        self.assertEqual(repaired.reference_plan_name, 'TOP 11GB 기본 (CU할인)')

    def test_duplicate_exact_current_plan_asks_for_its_price_not_another_benefit(self):
        """동일한 CU 상품의 가격만 다르면 CU를 찾았다고 알리고 납부액으로 구분한다."""
        from agent.agents.recommend import _resolve_reference

        profile = UserProfile(reference_plan_name='TOP 11GB 기본 (CU할인)')
        reference, question = _resolve_reference(profile)
        self.assertIsNone(reference)
        self.assertIn('CU할인', question)
        self.assertIn('7,700원', question)
        self.assertIn('16,500원', question)
        self.assertNotIn('네이버페이', question)

        selected, question = _resolve_reference(profile.model_copy(update={'reference_fee_won': 7700}))
        self.assertIsNone(question)
        self.assertEqual(selected['plan_name'], 'TOP 11GB 기본 (CU할인)')
        self.assertEqual(selected['discounted_fee'], 7700)

    def test_price_only_reply_selects_the_ambiguous_current_plan(self):
        """가격 확인 질문에 '월 7,700원짜리'라고만 답해도 예산이 아닌 현재 요금이다."""
        from agent.agents.profiling import _apply_ambiguous_reference_fee_reply

        profile = UserProfile(
            reference_plan_name='TOP 11GB 기본 (CU할인)',
            budget_max_won=7700,
        )
        fixed = _apply_ambiguous_reference_fee_reply(
            profile,
            '현재 요금제가 TOP 11GB 기본 (CU할인)이야\n월 7,700원짜리',
        )
        self.assertEqual(fixed.reference_fee_won, 7700)
        self.assertIsNone(fixed.budget_max_won)

    def test_all_empty_result_blocker_fields_are_relaxed_structurally(self):
        """조건 풀기 버튼은 LLM 해석과 무관하게 모든 blocker 필드를 실제로 비운다."""
        values = {
            'budget_min_won': 10000, 'budget_max_won': 30000,
            'min_data_gb': 100.0, 'min_monthly_base_data_gb': 11.0,
            'min_daily_data_gb': 2.0, 'max_data_gb': 10.0,
            'data_unlimited': True, 'require_full_unlimited': True,
            'min_qos_mbps': 5.0, 'requires_qos': True,
            'min_tethering_gb': 50.0, 'min_voice_minutes': 300,
            'voice_unlimited': True, 'sms_unlimited': True,
            'carrier_type': 'MVNO', 'host_mno': 'KT', 'mvno_brand': 'KT엠모바일',
            'network_gen': '5G', 'age_condition': '만 34세 이하',
            'wanted_benefits': ['넷플릭스'],
            'wanted_benefit_categories': ['영상/OTT'],
            'min_discount_period_months': 24,
        }
        self.assertEqual(set(values), set(CONSTRAINT_LABELS))
        profile = UserProfile(**values, hard_constraints=list(values))
        relaxed = _apply_relaxed_fields(profile, list(values))
        for field in values:
            with self.subTest(field=field):
                self.assertIsNone(getattr(relaxed, field))
                self.assertNotIn(field, relaxed.hard_constraints)

        widened = _apply_relaxed_fields(UserProfile(), ['include_mno'])
        self.assertTrue(widened.include_mno)

    def test_daily_allowance_is_not_monthly_data_or_daily_usage(self):
        offered = _apply_daily_allowance(
            UserProfile(min_data_gb=5), '하루에 데이터 5GB씩 주는 요금제 추천해줘 월 2만원 이하'
        )
        self.assertEqual(offered.min_daily_data_gb, 5)
        self.assertIsNone(offered.min_data_gb)
        self.assertEqual(_apply_daily_allowance(UserProfile(), '매일 3GB 제공').min_daily_data_gb, 3)
        self.assertEqual(_apply_daily_allowance(UserProfile(), '일일 2GB 지급').min_daily_data_gb, 2)

        combined = _apply_daily_allowance(
            UserProfile(min_data_gb=11), '월 11GB+일 2GB 주는 요금제'
        )
        self.assertEqual(combined.min_monthly_base_data_gb, 11)
        self.assertEqual(combined.min_daily_data_gb, 2)
        self.assertIsNone(combined.min_data_gb)

        reversed_order = _apply_daily_allowance(UserProfile(), '매일 2GB + 월 기본 11GB')
        self.assertEqual(reversed_order.min_daily_data_gb, 2)
        self.assertEqual(reversed_order.min_monthly_base_data_gb, 11)

        monthly_only = _apply_daily_allowance(
            UserProfile(min_monthly_base_data_gb=11), '월 기본 11GB 이상 추천해줘'
        )
        self.assertIsNone(monthly_only.min_monthly_base_data_gb)
        self.assertEqual(monthly_only.min_data_gb, 11)

        consumed = _apply_daily_allowance(
            UserProfile(min_data_gb=5, min_daily_data_gb=5), '하루에 5GB를 쓴다'
        )
        self.assertIsNone(consumed.min_daily_data_gb)
        self.assertIsNone(consumed.min_data_gb)
        self.assertEqual(consumed.target_data_gb, 150)

        self.assertEqual(
            _inferred_relaxed_fields([Message(role='user', content='매일 제공 데이터 조건은 빼고 추천해줘')]),
            ['min_daily_data_gb'],
        )

    def test_daily_allowance_filters_real_structure_and_can_be_relaxed(self):
        profile = {
            'budget_max_won': 20_000,
            'min_daily_data_gb': 5,
            'hard_constraints': ['budget_max_won', 'min_daily_data_gb'],
        }
        candidates = filter_candidates(profile)
        self.assertTrue(candidates)
        self.assertTrue(all(row['daily_data_gb'] is not None and row['daily_data_gb'] >= 5
                            and row['discounted_fee'] <= 20_000 for row in candidates))
        combined = filter_candidates({**profile, 'min_daily_data_gb': 2,
                                      'min_monthly_base_data_gb': 11})
        self.assertTrue(combined)
        self.assertTrue(all(row['daily_data_gb'] >= 2 and
                            row['data_gb'] - row['daily_data_gb'] * 30 >= 11 - 1e-6
                            for row in combined))

        impossible = {**profile, 'min_daily_data_gb': 100}
        blockers = diagnose_empty(impossible)
        self.assertTrue(any(item['field'] == 'min_daily_data_gb' and item['candidates'] > 0
                            for item in blockers))
        relaxed = _apply_relaxed_fields(UserProfile(**impossible), ['min_daily_data_gb'])
        self.assertIsNone(relaxed.min_daily_data_gb)
        self.assertTrue(filter_candidates(relaxed.model_dump()))

    def test_relaxed_data_condition_is_not_restored_from_current_plan(self):
        """현재 요금제 비교의 자동 데이터 기준도 사용자가 풀면 다시 붙지 않아야 한다."""
        from agent.agents.recommend import _with_reference_baseline

        reference = {'data_unlimited': True, 'effective_unlimited': True}
        restored = _with_reference_baseline(UserProfile(), reference)
        self.assertTrue(restored.data_unlimited)
        relaxed = _with_reference_baseline(UserProfile(), reference, ['data_unlimited'])
        self.assertIsNone(relaxed.data_unlimited)

    def test_recommend_api_passes_structured_relaxations_to_graph(self):
        state = {'profile': UserProfile(), 'ranked': [], 'candidates': []}
        with patch('backend.main.graph.invoke', return_value=state) as invoke:
            response = self.client.post('/api/recommend', json={
                'messages': [{'role': 'user', 'content': '요청 혜택 조건은 빼줘'}],
                'relaxedFields': ['wanted_benefits'],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(invoke.call_args.args[0]['relaxed_fields'], ['wanted_benefits'])

    def test_typed_budget_ceiling_relaxation_stays_in_recommendation_flow(self):
        """예산 상한 해제 문장은 오프토픽이 아니며 정확한 필드 하나만 제거한다."""
        messages = [
            Message(role='user', content='통신 3사 넷플릭스 혜택 있는 요금제 추천해줘'),
            Message(role='assistant', content='월 데이터 사용량이나 희망 월 예산을 알려주세요.'),
            Message(role='user', content='월 5만원 이하'),
            Message(role='assistant', content='조건에 맞는 요금제를 찾지 못했어요.'),
            Message(role='user', content='예산 상한 조건은 빼고 다시 추천해줘'),
        ]
        self.assertIsNone(_quick_chat_response(messages))
        self.assertEqual(_inferred_relaxed_fields(messages), ['budget_max_won'])

    def test_app_usage_adds_seven_gb_without_changing_smartchoice_ranges(self):
        youtube, notes = estimate_monthly_data_gb(daily_usage_hours={'youtube': 1})
        smartchoice, _ = estimate_monthly_data_gb(smartchoice_usage_pattern='video_2h')
        self.assertEqual(youtube, 65.5)
        self.assertFalse(any('10%' in note for note in notes))
        self.assertEqual(smartchoice, 80.0)

    def test_unspecified_mobile_game_uses_specific_game_arithmetic_mean(self):
        expected_mean = round(
            sum(SPECIFIC_GAME_GB_PER_HOUR.values()) / len(SPECIFIC_GAME_GB_PER_HOUR),
            3,
        )
        game, notes = estimate_monthly_data_gb(daily_game_hours=1)

        self.assertEqual(expected_mean, 0.068)
        self.assertEqual(AVERAGE_MOBILE_GAME_GB_PER_HOUR, expected_mean)
        self.assertEqual(game, 9.0)
        self.assertTrue(any('0.068GB/시간' in note for note in notes))

    def test_app_quality_qos_is_only_applied_for_post_exhaustion_use(self):
        plain = _apply_usage_based_qos(UserProfile(), '유튜브 HD 화질로 봐요')
        youtube = _apply_usage_based_qos(
            UserProfile(), '데이터 소진 후에도 유튜브 HD 화질로 보고 싶어요')
        netflix = _apply_usage_based_qos(
            UserProfile(), '소진 후 속도로 넷플릭스를 볼 수 있는 요금제')

        self.assertIsNone(plain.min_qos_mbps)
        self.assertIsNone(plain.requires_qos)
        self.assertEqual(youtube.min_qos_mbps, 3.0)
        self.assertTrue(youtube.requires_qos)
        self.assertEqual(netflix.min_qos_mbps, 3.0)
        self.assertTrue(netflix.requires_qos)

    def test_official_qos_thresholds_and_unsupported_apps_are_distinguished(self):
        youtube_1080 = _apply_usage_based_qos(
            UserProfile(), '소진 후에도 유튜브 1080p로 보고 싶어요')
        youtube_4k = _apply_usage_based_qos(
            UserProfile(), '소진 후에도 유튜브 4K로 보고 싶어요')
        netflix_4k = _apply_usage_based_qos(
            UserProfile(), '넷플릭스 4K를 데이터 소진 후에도 보고 싶어요')
        disney_live = _apply_usage_based_qos(
            UserProfile(), '디즈니+ 라이브를 소진 후에도 보고 싶어요')
        spotify = _apply_usage_based_qos(
            UserProfile(), 'Spotify 320kbps를 데이터 소진 후에도 듣고 싶어요')
        unsupported = _apply_usage_based_qos(
            UserProfile(min_qos_mbps=5), '소진 후에도 틱톡을 보고 싶어요')

        self.assertEqual(youtube_1080.min_qos_mbps, 5.0)
        self.assertEqual(youtube_4k.min_qos_mbps, 20.0)
        self.assertEqual(netflix_4k.min_qos_mbps, 15.0)
        self.assertEqual(disney_live.min_qos_mbps, 10.0)
        self.assertEqual(spotify.min_qos_mbps, 0.4)
        self.assertIsNone(unsupported.min_qos_mbps)
        self.assertTrue(unsupported.requires_qos)
        self.assertEqual(filter_candidates(youtube_4k.model_dump(exclude_none=True)), [])

    def test_explicit_qos_value_and_later_relaxation_override_app_inference(self):
        explicit_query = '소진 후에도 유튜브 HD로 보고 싶고 QoS 1Mbps 이상이면 돼'
        inferred = _apply_usage_based_qos(UserProfile(), explicit_query)
        explicit = _apply_explicit_qos_min(inferred, explicit_query)
        relaxed_query = '소진 후에도 유튜브 HD로 보고 싶어\nQoS 조건은 빼줘'
        relaxed = _apply_explicit_qos_min(
            _apply_usage_based_qos(UserProfile(), relaxed_query), relaxed_query)

        self.assertEqual(inferred.min_qos_mbps, 3.0)
        self.assertEqual(explicit.min_qos_mbps, 1.0)
        self.assertIsNone(relaxed.min_qos_mbps)

    def test_typed_benefit_relaxation_clears_pending_question(self):
        """직접 입력과 버튼 모두 혜택 유형을 빼고 추가 질문 없이 다시 추천한다."""
        profile = UserProfile(
            budget_max_won=40000,
            wanted_benefit_categories=['스마트기기'],
            hard_constraints=['budget_max_won', 'wanted_benefit_categories'],
            needs_user_input=True,
            followup_question=BENEFIT_PREFERENCE_QUESTION,
            ambiguous=['benefit_preference'],
        )
        relaxed = _apply_relaxed_fields(profile, ['wanted_benefit_categories'])
        self.assertIsNone(relaxed.wanted_benefit_categories)
        self.assertFalse(relaxed.needs_user_input)
        self.assertIsNone(relaxed.followup_question)
        self.assertFalse(benefit_preference_missing(relaxed))
        self.assertTrue(filter_candidates(relaxed.model_dump(exclude_none=True)))

        state = {'profile': relaxed, 'ranked': [], 'candidates': []}
        with patch('backend.main.graph.invoke', return_value=state) as invoke:
            response = self.client.post('/api/recommend', json={
                'messages': [
                    {'role': 'user', 'content': '하루에 유튜브 한시간봐. 부가혜택을 중요시해. 월 4만원 이하'},
                    {'role': 'assistant', 'content': '어떤 혜택을 찾으시나요?'},
                    {'role': 'user', 'content': '스마트기기'},
                    {'role': 'assistant', 'content': '조건에 맞는 요금제를 찾지 못했어요.'},
                    {'role': 'user', 'content': '아니 혜택 유형을 빼고 다시 추천해달라고'},
                ],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(invoke.call_args.args[0]['relaxed_fields'], ['wanted_benefit_categories'])

    def test_unrelated_questions_stay_in_chat_without_llm(self):
        for question in ('손흥민 알아?', '52-4는 뭐야?', '한국의 수도가 어디야?', '나 너 좋아해'):
            with self.subTest(question=question), patch('backend.main.graph.invoke') as invoke:
                response = self.client.post('/api/recommend', json={
                    'messages': [{'role': 'user', 'content': question}],
                })
                invoke.assert_not_called()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['conversationOnly'])
            self.assertEqual(response.json()['conversationKind'], 'off_topic')
            self.assertIn('요금제와 관련되지 않은 질문', response.json()['assistantMessage'])

        with patch('backend.main.graph.invoke') as invoke:
            response = self.client.post('/api/recommend', json={'messages': [
                {'role': 'user', 'content': '월 3만원 이하로 추천해줘'},
                {'role': 'assistant', 'content': '3개의 요금제를 찾았어요.'},
                {'role': 'user', 'content': '손흥민 알아?'},
            ]})
            invoke.assert_not_called()
        self.assertEqual(response.json()['conversationKind'], 'off_topic')

        personal_chat = _quick_chat_response([
            Message(role='user', content='월 3만원 이하로 추천해줘'),
            Message(role='assistant', content='3개의 요금제를 찾았어요.'),
            Message(role='user', content='나 너 좋아해'),
        ])
        self.assertEqual(personal_chat['assistantMessage'],
                         '모모플랜은 휴대폰 요금제 비교를 도와드려요. 요금제와 관련되지 않은 질문에는 답할 수 없습니다.')

        # 혜택 유형과 나이처럼 짧지만 실제로 추천에 필요한 답변은 계속 분석한다.
        self.assertIsNone(_quick_chat_response([
            Message(role='assistant', content=BENEFIT_PREFERENCE_QUESTION),
            Message(role='user', content='교육'),
        ]))
        self.assertIsNone(_quick_chat_response([
            Message(role='assistant', content='연령을 알려주세요.'),
            Message(role='user', content='20대'),
        ]))
        self.assertIsNone(_quick_chat_response([
            Message(role='user', content='축구 볼 때 데이터 많이 써. 요금제 추천해줘'),
        ]))
        self.assertIsNone(_quick_chat_response([
            Message(role='user', content='하루 한시간 게임한다'),
        ]))
        self.assertEqual(_quick_chat_response([
            Message(role='user', content='게임 추천해줘'),
        ])['conversationKind'], 'off_topic')

    def test_plan_information_question_confirms_scope_then_answers(self):
        with patch('backend.main.graph.invoke') as invoke:
            first = self.client.post('/api/recommend', json={
                'messages': [{'role': 'user', 'content': '가장 싼 요금제가 뭐야?'}],
            })
            second = self.client.post('/api/recommend', json={
                'messages': [
                    {'role': 'user', 'content': '가장 싼 요금제가 뭐야?'},
                    {'role': 'assistant', 'content': first.json()['assistantMessage']},
                    {'role': 'user', 'content': '알뜰폰만'},
                ],
            })
            expensive_first = self.client.post('/api/recommend', json={
                'messages': [{'role': 'user', 'content': '제일 비싼 요금제가 뭐야?'}],
            })
            expensive_second = self.client.post('/api/recommend', json={
                'messages': [
                    {'role': 'user', 'content': '제일 비싼 요금제가 뭐야?'},
                    {'role': 'assistant', 'content': expensive_first.json()['assistantMessage']},
                    {'role': 'user', 'content': '전체'},
                ],
            })
            network = self.client.post('/api/recommend', json={
                'messages': [
                    {'role': 'user', 'content': '5G 요금제 뭐 있어?'},
                    {'role': 'assistant', 'content': first.json()['assistantMessage']},
                    {'role': 'user', 'content': '알뜰폰만'},
                ],
            })
            invoke.assert_not_called()
        self.assertEqual(first.json()['conversationKind'], 'plan_info')
        self.assertEqual(
            first.json()['assistantMessage'],
            '알뜰폰, 통신 3사, 전체 요금제 중 어떤 범위에서 찾아볼까요?',
        )
        self.assertEqual(second.json()['conversationKind'], 'plan_info')
        self.assertIn('초기 월 요금 기준으로 가장 싼', second.json()['assistantMessage'])
        self.assertEqual(expensive_first.json()['assistantMessage'], first.json()['assistantMessage'])
        self.assertIn('초기 월 요금 기준으로 가장 비싼', expensive_second.json()['assistantMessage'])
        self.assertIn('5G 요금제는', network.json()['assistantMessage'])

    def test_latest_followup_can_remove_minimum_qos_constraint(self):
        """0건 화면의 '조건 풀기' 문장이 이전 QoS 수치를 실제로 지워야 한다."""
        conversation = (
            '월 3만원 이하, 소진 후 속도 5Mbps 이상으로 추천해줘\n'
            '소진 후 최소 속도 조건은 빼고 다시 추천해줘'
        )
        relaxed = _apply_explicit_qos_min(UserProfile(min_qos_mbps=5), conversation)
        self.assertIsNone(relaxed.min_qos_mbps)

        # 조건을 푼 뒤 사용자가 새 속도를 지정하면 가장 최신 수치가 다시 적용된다.
        changed = _apply_explicit_qos_min(
            relaxed,
            conversation + '\n소진 후 속도 1Mbps 이상으로 다시 찾아줘',
        )
        self.assertEqual(changed.min_qos_mbps, 1)

    def test_card_reason_hides_missing_data_notice(self):
        from agent.agents.report import _clean_card_reason

        reason = ('같은 가격대 후보보다 데이터가 많아 영상 시청에 유리합니다. '
                  '다만 소진 후 속도는 미수집입니다. 정보 확인이 필요합니다.')
        cleaned = _clean_card_reason(reason)
        self.assertIn('영상 시청에 유리', cleaned)
        self.assertNotIn('미수집', cleaned)
        self.assertNotIn('확인', cleaned)

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

    def test_daily_allowance_plan_name_does_not_shrink_current_data(self):
        """상품명의 11GB를 추출해도 월 11GB+매일 2GB를 11GB로 덮지 않는다."""
        reference, question = _resolve_reference(UserProfile(
            reference_plan_name='TOP 11GB 기본 (밀리의서재)',
            reference_fee_won=16500,
            reference_data_gb=11,
        ))
        self.assertIsNone(question)
        self.assertEqual(reference['base_data_gb'], 11)
        self.assertEqual(reference['data_gb'], 71)
        delta = reference_delta(reference, reference)
        self.assertEqual(delta['currentData'], '월 기본 11GB + 매일 2GB')
        self.assertEqual(delta['candidateData'], '월 기본 11GB + 매일 2GB')
        self.assertEqual(delta['dataDiffGb'], 0)

    def test_report_uses_monthly_equivalent_for_daily_reference_plan(self):
        """리포트 입력·문장 모두 일 제공형 현재 요금제를 11GB로 축소하지 않는다."""
        from agent.agents.report import (
            _current_plan_comparison, _repair_daily_reference_claim, _report_profile,
        )

        profile = UserProfile(
            reference_plan_name='TOP 11GB 기본 (밀리의서재)',
            reference_fee_won=16500,
            reference_data_gb=11,
        )
        reference, question = _resolve_reference(profile)
        self.assertIsNone(question)
        context = _current_plan_comparison(reference)
        self.assertEqual(context['monthly_equivalent_data_gb'], 71)
        self.assertEqual(context['data_display'], '월 기본 11GB + 매일 2GB (30일 기준 월 환산 약 71GB)')
        self.assertNotIn('reference_data_gb', _report_profile(profile))
        repaired = _repair_daily_reference_claim(
            '현재의 기본 11GB보다 많은 100GB를 쓰면서 소진 후 속도도 높습니다.', reference,
        )
        self.assertIn('현재 요금제의 월 환산 약 71GB보다 많은 100GB', repaired)

    def test_reference_name_gb_is_not_a_new_minimum_and_goal_survives_fee_reply(self):
        """상품명의 11GB를 후보 하한으로 쓰지 않고 최초의 '더 많은 데이터'를 보존한다."""
        conversation = (
            '현재 요금제가 TOP 11GB 기본 (밀리의서재) 이건데 이것보다 데이터 많고 '
            '좋은 요금제 추천해줘\n월 16,500원이야'
        )
        parsed = UserProfile(
            reference_plan_name='TOP 11GB 기본 (밀리의서재)',
            reference_fee_won=16500,
            reference_data_gb=11,
            min_data_gb=11,
        )
        repaired = _repair_general_comparison(parsed, conversation)
        repaired = _drop_reference_name_data_constraint(repaired, conversation)
        self.assertEqual(repaired.comparison_goals, ['more_data'])
        self.assertIsNone(repaired.min_data_gb)

        result = recommend_node({'profile': repaired}, {})
        self.assertEqual(len(result['candidates']), 401)
        self.assertTrue(all(plan['data_gb'] > 71 for plan in result['candidates']))
        self.assertEqual(result['reference_verdict']['status'], 'tradeoff')
        self.assertEqual(result['reference_verdict']['goal'], 'more_data')
        self.assertEqual(result['reference_verdict']['cheaperCount'], 0)

    def test_explicit_data_amount_outside_reference_name_is_preserved(self):
        profile = UserProfile(
            reference_plan_name='TOP 11GB 기본 (밀리의서재)',
            min_data_gb=50,
        )
        repaired = _drop_reference_name_data_constraint(
            profile,
            '현재 TOP 11GB 기본 (밀리의서재)를 쓰고 데이터 50GB 이상으로 추천해줘',
        )
        self.assertEqual(repaired.min_data_gb, 50)

    def test_generic_good_plan_does_not_ask_for_benefit_type(self):
        """'괜찮은 요금제'는 혜택 선호가 아니므로 추천 결과와 질문을 함께 보이지 않는다."""
        query = (
            '현재 요금제가 TOP 11GB 기본 (밀리의서재)이거인데 이것보다 데이터 많고 '
            '괜찮은 요금제 추천해줘 월 가격은 7700원이야'
        )
        overasked = UserProfile(
            reference_plan_name='TOP 11GB 기본 (밀리의서재)',
            reference_fee_won=7700,
            needs_user_input=True,
            followup_question='어떤 혜택이 포함되면 좋겠나요?',
            ambiguous=['benefit_preference'],
        )
        repaired = _drop_unrequested_benefit_followup(overasked, query)
        self.assertFalse(repaired.needs_user_input)
        self.assertIsNone(repaired.followup_question)
        self.assertNotIn('benefit_preference', repaired.ambiguous)

        # 반면 실제로 혜택의 좋고 나쁨을 묻는 요청은 추가 질문을 유지한다.
        explicit_benefit = _drop_unrequested_benefit_followup(
            overasked,
            '현재 요금제보다 혜택이 더 괜찮은 요금제를 추천해줘',
        )
        self.assertTrue(explicit_benefit.needs_user_input)

    def test_current_and_equivalent_benefit_variants_are_not_recommended(self):
        """현재 상품 및 가격·핵심 스펙이 같은 혜택 변형은 추천 자리를 차지하지 않는다."""
        reference, _ = _resolve_reference(
            UserProfile(reference_plan_name='TOP 11GB 기본 (밀리의서재)', reference_fee_won=16500)
        )
        same = next(row for row in self.rows if row['plan_id'] == reference['plan_id'])
        cu = next(row for row in self.rows
                  if row['plan_name'] == 'TOP 11GB 기본 (CU할인)' and row['discounted_fee'] == 16500)
        cheaper_cu = next(row for row in self.rows
                          if row['plan_name'] == 'TOP 11GB 기본 (CU할인)' and row['discounted_fee'] == 7700)
        self.assertTrue(_same_or_equivalent_to_reference(same, reference))
        self.assertTrue(_same_or_equivalent_to_reference(cu, reference))
        self.assertFalse(_same_or_equivalent_to_reference(cheaper_cu, reference))

    def test_recommendation_pool_excludes_current_and_equivalent_variants(self):
        """동일 상품 제거 규칙이 실제 추천 후보 구성에도 적용된다."""
        result = recommend_node({'profile': UserProfile(
            reference_plan_name='TOP 11GB 기본 (밀리의서재)',
            reference_fee_won=16500,
            user_age=30,
        )}, {})
        reference = result['reference']
        self.assertIsNotNone(reference)
        self.assertTrue(result['candidates'])
        self.assertTrue(all(
            not _same_or_equivalent_to_reference(candidate, reference)
            for candidate in result['candidates']
        ))

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
        self.assertIsNone(delta['currentDiscountEndsAfterMonths'])
        self.assertIsNone(delta['currentFeeAfterDiscount'])
        self.assertEqual(sum(point['candidate'] for point in delta['schedule']), delta['candidateTotal'])
        self.assertEqual(len(delta['schedule']), COMPARE_MONTHS)

    def test_catalog_current_promo_schedule_is_used_for_12_month_total(self):
        """현재 실납부액이 카탈로그 프로모션가와 같으면 종료 후 정상가도 총비용에 반영한다."""
        from agent.agents.recommend import _with_current_facts

        current_plan = next(
            plan for plan in self.rows
            if plan['plan_name'] == 'TOP 11GB 기본 (밀리의서재)'
            and plan['discounted_fee'] == 7700
        )
        candidate = next(
            plan for plan in self.rows
            if plan['plan_name'] == 'A 5G 스페셜 슈퍼'
        )
        reference = _with_current_facts(
            current_plan,
            UserProfile(reference_plan_name=current_plan['plan_name'], reference_fee_won=7700),
        )
        self.assertEqual(reference['discount_period_months'], 7)
        self.assertEqual(reference['monthly_fee'], 38500)
        delta = reference_delta(reference, candidate)
        self.assertEqual(delta['currentTotal'], 7700 * 7 + 38500 * 5)
        self.assertEqual(delta['candidateTotal'], 6500 * 7 + 48420 * 5)
        self.assertEqual(delta['totalDiff'], 41200)
        self.assertEqual(delta['currentDiscountEndsAfterMonths'], 7)
        self.assertEqual(delta['currentFeeAfterDiscount'], 38500)
        self.assertNotIn('현재 요금제의 할인 종료 시점', delta['unknowns'])

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
            self.assertFalse(any('정보가 부족한 후보' in note for note in verdict['confirm']))
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
        # 노출 개수가 바뀌어 원래 상위권이 이미 다양하더라도, 다양화가 이를 악화시키면 안 된다.
        raw_characters = {_offer_character(by_id[d.plan_id]) for d in ordered[:TOP_N]}
        self.assertGreaterEqual(len(set(characters)), len(raw_characters))
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
