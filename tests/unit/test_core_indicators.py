"""Closed-candle semantics and published/hand-verifiable indicator vectors."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP, localcontext

import pytest

from tradebot_core.indicators import (
    adx,
    atr,
    bollinger_bands,
    donchian_channels,
    ema,
    keltner_channels,
    macd,
    pivot_points,
    rsi,
    sma,
    stochastic,
    supertrend,
    volume_sma,
    vwap,
    wma,
)
from tradebot_core.models import Candle


D = Decimal


def bars(prices, *, volumes=None, start=None):
    start = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
    volumes = volumes or ["1"] * len(prices)
    return [
        Candle(timestamp=start + timedelta(minutes=index), timeframe_seconds=60,
               open=D(str(price)), high=D(str(price)) + 1, low=D(str(price)) - 1,
               close=D(str(price)), volume=D(str(volumes[index])), closed=True)
        for index, price in enumerate(prices)
    ]


def assert_decimal(actual, expected, tolerance="0.000000000001"):
    assert actual is not None
    assert abs(actual - D(expected)) <= D(tolerance)


def test_moving_average_fixed_vectors_and_explicit_warmup():
    candles = bars([2, 3, 4, 5, 6])
    assert sma(candles, 3) == [None, None, D(3), D(4), D(5)]
    assert ema(candles, 3) == [None, None, D(3), D(4), D(5)]
    weighted = wma(candles, 3)
    assert weighted[:2] == [None, None]
    assert_decimal(weighted[2], "3.333333333333333333333333333333333")
    assert_decimal(weighted[4], "5.333333333333333333333333333333333")
    assert sma(candles, 1) == [D(i) for i in range(2, 7)]
    assert ema(candles, 1) == [D(i) for i in range(2, 7)]


def test_ema_uses_sma_seed_and_not_first_price_seed():
    # Three-period EMA alpha=1/2: mean(2,8,5)=5; next=(5+11)/2=8.
    assert ema(bars([2, 8, 5, 11, 7]), 3) == [None, None, D(5), D(8), D("7.5")]


def test_rsi_matches_wilder_published_worksheet():
    # StockCharts ChartSchool RSI calculation worksheet (Wilder smoothing).
    prices = ["44.34", "44.09", "44.15", "43.61", "44.33", "44.83", "45.10",
              "45.42", "45.84", "46.08", "45.89", "46.03", "45.61", "46.28",
              "46.28", "46.00", "46.03", "46.41", "46.22", "45.64", "46.21"]
    values = rsi(bars(prices), 14)
    assert values[:14] == [None] * 14
    for actual, expected in zip(values[14:], ["70.46", "66.25", "66.48", "69.35", "66.29", "57.92", "62.88"]):
        assert_decimal(actual, expected, "0.005")


def test_rsi_one_direction_and_flat_market_are_defined():
    assert rsi(bars([2, 3, 4, 5]), 3)[-1] == D(100)
    assert rsi(bars([5, 4, 3, 2]), 3)[-1] == D(0)
    assert rsi(bars([5, 5, 5, 5]), 3)[-1] == D(50)


def test_macd_fixed_linear_vector_and_signal_warmup():
    values = macd(bars([2, 3, 4, 5, 6, 7, 8]), fast=2, slow=4, signal=3)
    assert values[:3] == [None] * 3
    assert values[3].line == D(1)
    assert values[3].signal is None
    assert values[3].histogram is None
    assert values[4].signal is None
    assert values[5].line == values[5].signal == D(1)
    assert values[5].histogram == D(0)
    assert values[6].histogram == D(0)


def test_bollinger_uses_population_variance():
    values = bollinger_bands(bars([2, 3, 4, 5, 6]), 5)
    assert values[:4] == [None] * 4
    bands = values[4]
    # Population variance of 2..6 is 2, not the sample variance of 2.5.
    assert bands.middle == D(4)
    assert_decimal(bands.standard_deviation, "1.414213562373095048801688724209698")
    assert_decimal(bands.upper, "6.828427124746190097603377448419396")
    assert_decimal(bands.lower, "1.171572875253809902396622551580604")
    flat = bollinger_bands(bars([5, 5, 5]), 3)[-1]
    assert flat.lower == flat.middle == flat.upper == D(5)


def test_atr_captures_gaps_and_uses_wilder_smoothing():
    # Ranges: 2, 6 (previous close=5), 3, 2. Seed mean=11/3.
    candles = bars([5, 10, 8, 9])
    values = atr(candles, 3)
    assert values[:2] == [None, None]
    assert_decimal(values[2], "3.666666666666666666666666666666667")
    assert_decimal(values[3], "3.111111111111111111111111111111111")
    assert atr(candles, 1) == [D(2), D(6), D(3), D(2)]


def test_stochastic_extremes_and_separate_k_d_warmup():
    values = stochastic(bars([2, 3, 4, 5, 6, 7]), period=3, smooth_k=2, smooth_d=2)
    assert values[:3] == [None] * 3
    assert values[3].k == D(75)
    assert values[3].d is None
    assert values[4].k == values[4].d == D(75)
    flat = [c.model_copy(update={"high": D(5), "low": D(5)}) for c in bars([5, 5, 5])]
    assert stochastic(flat, 2, 1, 1)[-1].k == D(50)


def test_vwap_volume_weighting_and_no_volume_state():
    candles = bars([2, 3, 8, 4], volumes=[0, 1, 3, 0])
    assert vwap(candles) == [None, D(3), D("6.75"), D("6.75")]
    assert volume_sma(candles, 2) == [None, D("0.5"), D(2), D("1.5")]


def test_vwap_reset_uses_utc_calendar_day():
    candles = bars([2, 4, 8], start=datetime(2025, 1, 1, 23, 58, tzinfo=timezone.utc))
    assert vwap(candles, reset_daily=True) == [D(2), D(3), D(8)]


def test_donchian_previous_window_does_not_include_breakout_candle():
    candles = bars([5, 6, 7, 100])
    current = donchian_channels(candles, 3)
    previous = donchian_channels(candles, 3, previous_closed_window=True)
    assert current[:2] == [None, None]
    assert previous[:3] == [None, None, None]
    assert current[3].upper == D(101)
    assert previous[3].upper == D(8)
    assert previous[3].lower == D(4)
    assert previous[3].middle == D(6)


def test_adx_directional_reference_vectors_and_double_warmup():
    # In a perfectly rising series, +DM=1 and TR=2: +DI=50, -DI=0,
    # DX=100. Wilder smoothing leaves these constants unchanged.
    values = adx(bars([2, 3, 4, 5, 6, 7, 8]), period=3)
    assert values[:3] == [None] * 3
    assert values[3].plus_di == D(50)
    assert values[3].minus_di == D(0)
    assert values[3].adx is None
    assert values[4].adx is None
    assert values[5].adx == values[6].adx == D(100)
    falling = adx(bars([8, 7, 6, 5, 4, 3, 2]), 3)[-1]
    assert falling.plus_di == D(0)
    assert falling.minus_di == D(50)
    assert falling.adx == D(100)
    flat = adx(bars([5] * 7), 3)[-1]
    assert flat.plus_di == flat.minus_di == flat.adx == D(0)


def test_adx_equal_outside_moves_cancel_both_directions():
    candles = bars([5, 5])
    candles[1] = candles[1].model_copy(update={"high": D(7), "low": D(3)})
    values = adx(candles, period=1)
    assert values[0] is None
    assert values[1].plus_di == values[1].minus_di == values[1].adx == D(0)


def test_keltner_uses_ema_and_independently_warmed_atr():
    values = keltner_channels(bars([5, 6, 7, 8]), period=3, atr_period=2, multiplier="2")
    assert values[:2] == [None, None]
    assert values[2].middle == D(6)
    assert values[2].upper == D(10)
    assert values[2].lower == D(2)
    assert values[3].middle == D(7)
    assert values[3].upper == D(11)
    assert values[3].lower == D(3)
    assert keltner_channels(bars([5, 6, 7]), period=2, atr_period=4) == [None] * 3


def test_supertrend_closes_cross_trailing_bands_and_direction_changes():
    # Includes a gap from 9 to 5: its true range is 5, not high-low=2.
    values = supertrend(bars([5, 6, 8, 9, 5, 4]), period=1, multiplier=1)
    assert [value.direction for value in values] == [-1, -1, 1, 1, -1, -1]
    assert [value.value for value in values] == [D(7), D(7), D(5), D(7), D(10), D(6)]
    assert supertrend(bars([5, 6]), period=3) == [None, None]


def test_supertrend_equal_to_upper_band_does_not_reverse():
    values = supertrend(bars([5, 7]), period=1, multiplier=1)
    assert values[1].upper == D(7)
    assert values[1].direction == -1


def test_classical_pivots_use_previous_candle_when_explicitly_requested():
    values = pivot_points(bars([5, 100]), session_seconds=None)
    assert values[0] is None
    levels = values[1]
    assert levels.pivot == D(5)
    assert (levels.r1, levels.r2, levels.r3) == (D(6), D(7), D(8))
    assert (levels.s1, levels.s2, levels.s3) == (D(4), D(3), D(2))


def test_daily_pivots_need_a_complete_previous_utc_session():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = [
        Candle(timestamp=start + timedelta(hours=6 * index), timeframe_seconds=21600,
               open=D(price), high=D(price + 1), low=D(price - 1), close=D(price), volume=D(1))
        for index, price in enumerate([10, 12, 14, 16, 100, 90, 80, 70, 60])
    ]
    values = pivot_points(candles)
    assert values[:4] == [None] * 4
    assert values[4] == values[5] == values[6] == values[7]
    assert values[4].pivot == D(14)
    assert (values[4].r1, values[4].r2, values[4].r3) == (D(19), D(22), D(27))
    assert (values[4].s1, values[4].s2, values[4].s3) == (D(11), D(6), D(3))
    # Starting at 06:00 excludes part of the first day; its levels are unknown.
    partial = pivot_points(candles[1:])
    assert partial[:7] == [None] * 7
    assert partial[7] is not None
    with pytest.raises(ValueError, match="multiple"):
        pivot_points(candles, session_seconds=20000)


INDICATORS = [
    lambda c: sma(c, 3), lambda c: ema(c, 3), lambda c: wma(c, 3),
    lambda c: rsi(c, 3), lambda c: macd(c, 2, 4, 2),
    lambda c: bollinger_bands(c, 3), lambda c: atr(c, 3),
    lambda c: stochastic(c, 3, 1, 1), vwap, lambda c: volume_sma(c, 3),
    lambda c: donchian_channels(c, 3),
    lambda c: adx(c, 3), lambda c: keltner_channels(c, 3, 2),
    lambda c: supertrend(c, 3, 1), lambda c: pivot_points(c, session_seconds=None),
    lambda c: pivot_points(c, session_seconds=180),
]


@pytest.mark.parametrize("indicator", INDICATORS)
def test_open_candle_never_influences_indicator(indicator):
    closed = bars([3, 4, 5, 6, 7, 8, 9])
    open_candle = bars([999], start=closed[-1].timestamp + timedelta(minutes=1))[0]
    open_candle = open_candle.model_copy(update={"closed": False})
    assert indicator(closed + [open_candle]) == indicator(closed) + [None]


@pytest.mark.parametrize("indicator", INDICATORS)
def test_empty_input_preserves_alignment(indicator):
    assert indicator([]) == []


@pytest.mark.parametrize("indicator", INDICATORS)
def test_future_candles_cannot_change_earlier_results(indicator):
    prefix = bars([3, 4, 8, 6, 7, 3, 5])
    future = bars([100, 2, 55], start=prefix[-1].timestamp + timedelta(minutes=1))
    assert indicator(prefix + future)[:len(prefix)] == indicator(prefix)


@pytest.mark.parametrize("indicator", INDICATORS)
def test_gaps_duplicates_interior_open_and_mixed_timeframes_rejected(indicator):
    candles = bars([3, 4, 5, 6, 7, 8])
    with pytest.raises(ValueError, match="contiguous"):
        indicator(candles[:2] + candles[3:])
    with pytest.raises(ValueError, match="unique"):
        indicator(candles[:2] + candles[1:])
    with pytest.raises(ValueError, match="closed"):
        indicator([candles[0].model_copy(update={"closed": False})] + candles[1:])
    with pytest.raises(ValueError, match="timeframes"):
        indicator(candles[:-1] + [candles[-1].model_copy(update={"timeframe_seconds": 300})])


@pytest.mark.parametrize("function", [sma, ema, wma, rsi, atr, bollinger_bands, stochastic, volume_sma,
                                      donchian_channels, adx, keltner_channels, supertrend])
@pytest.mark.parametrize("period", [0, -1, True, 2.5, "3"])
def test_invalid_period_rejected(function, period):
    with pytest.raises(ValueError, match="positive integer"):
        function([], period=period)


@pytest.mark.parametrize("multiplier", [0, -2, "NaN", "Infinity", 2.0, True])
def test_invalid_bollinger_multiplier_rejected(multiplier):
    with pytest.raises(ValueError):
        bollinger_bands([], stddev=multiplier)


@pytest.mark.parametrize("function", [keltner_channels, supertrend])
@pytest.mark.parametrize("multiplier", [0, -2, "NaN", "Infinity", 2.0, True])
def test_invalid_band_multiplier_rejected(function, multiplier):
    with pytest.raises(ValueError):
        function([], multiplier=multiplier)


def test_invalid_macd_stochastic_and_source_parameters_rejected():
    with pytest.raises(ValueError, match="smaller"):
        macd([], fast=26, slow=12)
    with pytest.raises(ValueError):
        macd([], signal=0)
    with pytest.raises(ValueError):
        stochastic([], smooth_k=0)
    with pytest.raises(ValueError):
        stochastic([], smooth_d=0)
    with pytest.raises(ValueError, match="source"):
        sma([], source="unknown")
    with pytest.raises(ValueError):
        vwap([], reset_daily="yes")
    with pytest.raises(ValueError):
        donchian_channels([], previous_closed_window="yes")
    with pytest.raises(ValueError):
        keltner_channels([], atr_period=0)
    with pytest.raises(ValueError):
        pivot_points([], session_seconds=0)


@pytest.mark.parametrize("indicator", INDICATORS)
def test_calculations_do_not_depend_on_process_decimal_context(indicator):
    candles = bars([3, 4, 8, 6, 7, 3, 5, 6, 5])
    expected = indicator(candles)
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_UP
        assert indicator(candles) == expected
