"""이용 시간으로 월 데이터 사용량을 결정적으로 추정한다."""

VIDEO_GB_PER_HOUR = 1.5
SHORTFORM_GB_PER_HOUR = 0.9
GAME_GB_PER_HOUR = 0.1
DAYS_PER_MONTH = 30
BUFFER_RATE = 1.1


def estimate_monthly_data_gb(
    daily_video_hours: float | None = None,
    daily_shortform_hours: float | None = None,
    daily_game_hours: float | None = None,
) -> tuple[float | None, list[str]]:
    """하루 이용 시간을 월 GB로 환산해 총량과 근거를 반환한다."""
    total = 0.0
    notes: list[str] = []
    usages = (
        ("일반 영상(1080p)", daily_video_hours, VIDEO_GB_PER_HOUR),
        ("숏폼", daily_shortform_hours, SHORTFORM_GB_PER_HOUR),
        ("모바일 게임", daily_game_hours, GAME_GB_PER_HOUR),
    )

    for label, hours, gb_per_hour in usages:
        if hours is None or hours <= 0:
            continue
        monthly = hours * gb_per_hour * DAYS_PER_MONTH
        total += monthly
        notes.append(f"{label} 하루 {hours:g}시간: 월 약 {monthly:.0f}GB")

    if total == 0:
        return None, []

    estimated = round(total * BUFFER_RATE)
    notes.append(f"여유분 10%를 포함해 월 약 {estimated}GB로 추정")
    return float(estimated), notes
