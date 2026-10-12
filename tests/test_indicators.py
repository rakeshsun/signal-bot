import math

import pytest

from goldbot import indicators as ta


def test_ema_seeds_with_sma_then_smooths():
    out = ta.ema([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)  # sma(1,2,3)
    assert out[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)  # alpha = 2/(3+1)
    assert out[4] == pytest.approx(0.5 * 5 + 0.5 * 3.0)


def test_rma_uses_wilder_alpha():
    out = ta.rma([2, 4, 6, 8], 2)
    assert out[1] == pytest.approx(3.0)
    assert out[2] == pytest.approx(0.5 * 6 + 0.5 * 3.0)


def test_ema_skips_leading_na():
    out = ta.ema([None, None, 1, 2, 3], 2)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(1.5)


def test_sma():
    assert ta.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_rsi_all_up_is_100_and_mixed_matches_hand_calc():
    assert ta.rsi([1, 2, 3, 4, 5], 2)[-1] == 100.0
    # changes: +1, -1, +2 ; length 2 -> seed after 2 changes: up=(1+0)/2, dn=(0+1)/2
    out = ta.rsi([10, 11, 10, 12], 2)
    up = 0.5 * 2 + 0.5 * 0.5
    dn = 0.5 * 0 + 0.5 * 0.5
    assert out[3] == pytest.approx(100 - 100 / (1 + up / dn))


def test_atr_first_tr_is_range():
    h, lo, c = [10, 12, 11], [8, 9, 7], [9, 11, 8]
    tr = ta.true_range(h, lo, c)
    assert tr == [2, 3, 4]
    assert ta.atr(h, lo, c, 2)[1] == pytest.approx(2.5)


def test_dmi_strong_uptrend_has_plus_di_above_minus_di():
    n = 80
    h = [100 + i + 1 for i in range(n)]
    lo = [100 + i - 1 for i in range(n)]
    c = [100 + i + 0.5 for i in range(n)]
    plus, minus, adx = ta.dmi(h, lo, c, 14, 14)
    assert plus[-1] > minus[-1]
    assert adx[-1] is not None and adx[-1] > 50
    assert not math.isnan(adx[-1])


def test_macd_hist_is_line_minus_signal():
    src = [float(x % 7 + x / 3) for x in range(60)]
    line, sig, hist = ta.macd(src)
    assert hist[-1] == pytest.approx(line[-1] - sig[-1])
    assert line[24] is None and line[25] is not None


def test_bars_since():
    assert ta.bars_since([False, True, False, False, True]) == [None, 0, 1, 2, 0]
