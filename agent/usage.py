"""앱·화질별 이용 시간 또는 스마트초이스 생활패턴으로 월 데이터량을 추정한다."""


SPECIFIC_GAME_GB_PER_HOUR = {
    "casual_game": 0.015,
    "pokemongo_game": 0.035,
    "moba_game": 0.075,
    "battle_royale_game": 0.075,
    "league_of_legend_game": 0.075,
    "battleground_game": 0.06,
    "clashroyale_game": 0.03,
    "online_rpg_game": 0.045,
    "fortnite_game": 0.125,
    "callofduty_game": 0.2,
    "brawlstars_game": 0.05,
    "stardewvalley_game": 0.03,
}
# 게임명이 없을 때는 위의 구체 게임 12종을 동일 가중치로 산술평균한다.
AVERAGE_MOBILE_GAME_GB_PER_HOUR = round(
    sum(SPECIFIC_GAME_GB_PER_HOUR.values()) / len(SPECIFIC_GAME_GB_PER_HOUR),
    3,
)

USAGE_PROFILES_GB_PER_HOUR = {
    "youtube": {"low_240p": 0.2, "sd_480p": 0.6, "hd_720p": 1.95, "fullhd_1440p": 8.5, "uhd_4k": 15.0},
    "netflix": {"low": 0.3, "sd": 0.7, "hd": 3.0, "uhd_4k": 7.0},
    "disney_plus": {"automatic_hd": 2.0},
    "tiktok": {"standard": 0.84, "hd": 1.8},
    "instagram": {"photo_feed": 0.12, "story_reels_mix": 0.6, "live": 1.2},
    "instagram_reels": {"reels": 0.9},
    "spotify": {"normal_96kbps": 0.04, "very_high_320kbps": 0.15, "lossless_hifi": 0.6},
    "google_maps": {"navigation": 0.005, "street_view": 0.05, "satellite": 0.15},
    "zoom": {"audio_only": 0.035, "one_to_one_sd": 0.54, "group_hd": 2.5},
    "whatsapp": {"text_voice": 0.04, "one_to_one_video": 0.25, "group_video": 0.5},
    "mobile_game": {"typical": AVERAGE_MOBILE_GAME_GB_PER_HOUR},
    **{
        service: {"typical": gb_per_hour}
        for service, gb_per_hour in SPECIFIC_GAME_GB_PER_HOUR.items()
    },
    "generic_video": {"standard": 1.8},
    "generic_shortform": {"standard": 0.9},
}

# 공식 권장 속도를 확인할 수 있는 서비스만 저장한다. DB에 정확한 속도 단계가 없으면
# 권장 속도 이상인 다음 QoS 단계로 올린다(예: 1.1Mbps -> 3Mbps).
# 10Mbps를 넘는 값은 현재 DB에서 일치 상품이 없으므로 해당 화질을 보장하지 않는다.
# 확인일: 2026-09-26
# YouTube: https://support.google.com/youtube/answer/78358
# Netflix: https://help.netflix.com/en/node/306
# Disney+: https://help.disneyplus.com/article/disneyplus-en-ph-recommended-speeds
# Zoom: https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0058323
# Spotify: https://support.spotify.com/au/article/audio-quality/
USAGE_REQUIRED_QOS_MBPS = {
    "youtube": {
        "sd_480p": 3.0,
        "hd_720p": 3.0,
        "fhd_1080p": 5.0,
        "uhd_4k": 20.0,
    },
    "netflix": {"hd": 3.0, "fhd_1080p": 5.0, "uhd_4k": 15.0},
    "disney_plus": {"automatic_hd": 5.0, "live": 10.0, "uhd_4k": 25.0},
    "spotify": {
        "normal_96kbps": 0.4,
        "very_high_320kbps": 0.4,
    },
    "zoom": {
        "audio_only": 0.4,
        "one_to_one_sd": 3.0,
        "group_hd": 3.0,
        "fhd_1080p": 5.0,
    },
}

SMARTCHOICE_USAGE_RANGES_GB = {
    "wifi_primary": (3.0, 6.0, "와이파이를 주로 사용"),
    "web_music_primary": (7.0, 15.0, "웹서핑·음악듣기를 주로 사용"),
    "video_1h": (20.0, 37.0, "하루 약 1시간 영상 시청"),
    "video_2h": (50.0, 80.0, "하루 약 2시간 영상 시청"),
    "video_3h_plus": (90.0, None, "하루 3시간 이상 영상 시청"),
}

DEFAULT_USAGE_MODE = {
    "youtube": "hd_720p", "netflix": "hd", "disney_plus": "automatic_hd",
    "tiktok": "standard", "instagram": "story_reels_mix", "instagram_reels": "reels",
    "spotify": "normal_96kbps", "google_maps": "navigation", "zoom": "one_to_one_sd",
    "whatsapp": "text_voice", "mobile_game": "typical", "casual_game": "typical",
    "pokemongo_game": "typical", "moba_game": "typical", "battle_royale_game": "typical",
    "league_of_legend_game": "typical", "battleground_game": "typical",
    "clashroyale_game": "typical", "online_rpg_game": "typical", "fortnite_game": "typical",
    "callofduty_game": "typical", "brawlstars_game": "typical", "stardewvalley_game": "typical",
    "generic_video": "standard", "generic_shortform": "standard",
}


def required_qos_mbps(service: str, mode: str | None = None) -> float | None:
    """앱과 화질에 맞는 소진 후 최소 속도를 반환한다."""
    modes = USAGE_REQUIRED_QOS_MBPS.get(service)
    if not modes:
        return None
    selected_mode = mode or DEFAULT_USAGE_MODE.get(service)
    return modes.get(selected_mode or "")

USAGE_MODE_LABELS = {
    "low_240p": "저화질 240p", "sd_480p": "SD 480p", "hd_720p": "HD 720p",
    "fullhd_1440p": "1440p", "uhd_4k": "4K UHD", "standard": "표준",
    "hd": "고화질 HD", "low": "저화질", "sd": "표준 SD", "automatic_hd": "자동 HD",
    "photo_feed": "사진 피드", "story_reels_mix": "스토리·릴스 혼합", "live": "라이브",
    "reels": "릴스", "normal_96kbps": "일반 96kbps", "very_high_320kbps": "매우 높음 320kbps",
    "lossless_hifi": "무손실·Hi-Fi", "navigation": "내비게이션", "street_view": "스트리트 뷰",
    "satellite": "위성 사진", "audio_only": "오디오 전용", "one_to_one_sd": "1:1 영상 SD",
    "group_hd": "그룹 HD 영상", "text_voice": "문자·음성 통화", "one_to_one_video": "1:1 영상 통화",
    "group_video": "그룹 영상 통화", "typical": "일반",
}

USAGE_LABELS = {
    "youtube": "유튜브", "netflix": "넷플릭스", "disney_plus": "디즈니+",
    "tiktok": "틱톡", "instagram": "인스타그램", "instagram_reels": "인스타그램 릴스",
    "spotify": "스포티파이", "google_maps": "구글 지도", "zoom": "Zoom",
    "whatsapp": "WhatsApp", "mobile_game": "모바일 게임", "casual_game": "캐주얼 게임",
    "pokemongo_game": "포켓몬 GO", "moba_game": "MOBA 게임",
    "battle_royale_game": "배틀로얄 게임", "league_of_legend_game": "리그 오브 레전드",
    "battleground_game": "배틀그라운드", "clashroyale_game": "클래시 로얄",
    "online_rpg_game": "온라인 RPG", "fortnite_game": "포트나이트",
    "callofduty_game": "콜 오브 듀티", "brawlstars_game": "브롤스타즈",
    "stardewvalley_game": "스타듀 밸리", "generic_video": "일반 영상",
    "generic_shortform": "숏폼",
}

QUALITY_BASED_SERVICES = {
    "youtube", "netflix", "disney_plus", "tiktok", "instagram", "instagram_reels", "zoom"
}
DAYS_PER_MONTH = 30
# 앱 사용시간으로 포착되지 않는 웹 서핑·메신저 등의 월 기본 사용량.
BASELINE_GENERAL_DATA_GB = 7.0


def estimate_monthly_data_gb(
    daily_video_hours: float | None = None,
    daily_shortform_hours: float | None = None,
    daily_game_hours: float | None = None,
    daily_usage_hours: dict[str, float] | None = None,
    usage_modes: dict[str, str] | None = None,
    smartchoice_usage_pattern: str | None = None,
) -> tuple[float | None, list[str]]:
    """월 예상량과 사용자에게 보여줄 계산 근거를 반환한다."""
    total = 0.0
    notes: list[str] = []
    usages = dict(daily_usage_hours or {})
    selected_modes = dict(usage_modes or {})

    smartchoice = SMARTCHOICE_USAGE_RANGES_GB.get(smartchoice_usage_pattern or "")
    if smartchoice and not usages:
        lower, upper, label = smartchoice
        range_text = f"{lower:g}~{upper:g}GB" if upper is not None else f"{lower:g}GB 이상"
        filter_target = upper if upper is not None else lower
        notes.append(
            f"스마트초이스 이용 패턴을 참고하면 {label}의 월 데이터 사용량은 "
            f"{range_text}로 예상됩니다."
        )
        if upper is not None:
            notes.append(
                f"데이터가 부족하지 않도록 예상 범위의 상한인 월 {filter_target:g}GB 이상 "
                "제공하는 요금제를 추천합니다."
            )
        else:
            notes.append(f"월 {filter_target:g}GB 이상 제공하는 요금제를 추천합니다.")
        return float(filter_target), notes

    # 기존 세 범주도 지원하되 앱별 입력과 같은 활동을 중복 합산하지 않는다.
    if daily_video_hours and not {"youtube", "netflix", "disney_plus", "generic_video"}.intersection(usages):
        usages["generic_video"] = daily_video_hours
    if daily_shortform_hours and not {"instagram_reels", "tiktok", "generic_shortform"}.intersection(usages):
        usages["generic_shortform"] = daily_shortform_hours
    if daily_game_hours and not any(service.endswith("_game") for service in usages):
        usages["mobile_game"] = daily_game_hours

    default_mode_labels: list[str] = []
    for service, hours in usages.items():
        profiles = USAGE_PROFILES_GB_PER_HOUR.get(service)
        if not profiles or hours is None or hours <= 0:
            continue
        default_mode = DEFAULT_USAGE_MODE[service]
        mode = selected_modes.get(service) or default_mode
        if mode not in profiles:
            mode = default_mode
        gb_per_hour = profiles[mode]
        if service not in selected_modes and service in QUALITY_BASED_SERVICES:
            default_mode_labels.append(f"{USAGE_LABELS[service]}는 {USAGE_MODE_LABELS.get(mode, mode)}")
        monthly = float(hours) * gb_per_hour * DAYS_PER_MONTH
        total += monthly
        notes.append(
            f"{USAGE_LABELS[service]}({USAGE_MODE_LABELS.get(mode, mode)}) "
            f"하루 {hours:g}시간 × {gb_per_hour:g}GB/시간: 월 약 {monthly:.1f}GB"
        )

    if total == 0:
        return None, []

    if default_mode_labels:
        notes.insert(0, f"화질을 별도로 말씀하지 않아 {', '.join(default_mode_labels)} 기준으로 계산")
    estimated = round(total + BASELINE_GENERAL_DATA_GB, 1)
    return float(estimated), notes


if __name__ == "__main__":
    youtube, youtube_notes = estimate_monthly_data_gb(daily_usage_hours={"youtube": 1})
    assert youtube == 65.5, (youtube, youtube_notes)
    assert any("HD 720p" in note for note in youtube_notes)
    assert not any("10%" in note for note in youtube_notes)

    netflix_4k, _ = estimate_monthly_data_gb(
        daily_usage_hours={"netflix": 1}, usage_modes={"netflix": "uhd_4k"}
    )
    assert netflix_4k == 217.0, netflix_4k

    no_duplicate, _ = estimate_monthly_data_gb(
        daily_video_hours=2, daily_usage_hours={"youtube": 1}
    )
    assert no_duplicate == 65.5, no_duplicate

    smartchoice, smartchoice_notes = estimate_monthly_data_gb(smartchoice_usage_pattern="video_2h")
    assert smartchoice == 80.0 and "50~80GB" in smartchoice_notes[0]
    assert "상한인 월 80GB 이상" in smartchoice_notes[1]
    print("self-check ok: app and SmartChoice data estimation")
