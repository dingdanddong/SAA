"""기술적 지표 (Agent 3 정량 분석용 순수 함수).

입력 df: 과거 → 최근 순 일봉, 컬럼 date/open/high/low/close/volume.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

# KRX 호가단위 (2023-01-25 개편, KOSPI·KOSDAQ 공통)
_TICKS = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))


def tick_size(price: float) -> int:
    for limit, tick in _TICKS:
        if price < limit:
            return tick
    return 1_000


def round_tick(price: float, mode: str = "nearest") -> int:
    tick = tick_size(price)
    q = price / tick
    if mode == "down":
        q = math.floor(q)
    elif mode == "up":
        q = math.ceil(q)
    else:
        q = round(q)
    return int(q * tick)


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """와일더 평활: 첫 값은 단순평균, 이후 (prev*(n-1)+x)/n."""
    out = np.full(len(values), np.nan)
    if len(values) < period:
        return out
    out[period - 1] = values[:period].mean()
    for i in range(period, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) <= period:
        return None
    delta = close.diff().to_numpy()[1:]
    gain = _wilder(np.clip(delta, 0, None), period)[-1]
    loss = _wilder(np.clip(-delta, 0, None), period)[-1]
    if np.isnan(gain) or np.isnan(loss):
        return None
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return float(100 - 100 / (1 + gain / loss))


def atr(df: pd.DataFrame, period: int = 14) -> float | None:
    if len(df) <= period:
        return None
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    value = _wilder(tr.to_numpy()[1:], period)[-1]
    return None if np.isnan(value) else float(value)


def moving_averages(close: pd.Series) -> dict[str, float | None]:
    return {
        f"ma{n}": (float(close.iloc[-n:].mean()) if len(close) >= n else None) for n in (5, 20, 60, 120)
    }


def ma_state(mas: dict[str, float | None]) -> str:
    """정배열 (5>20>60>120) / 역배열 / 혼조. 상장 기간이 짧으면 있는 이평선만 비교."""
    values = [mas[k] for k in ("ma5", "ma20", "ma60", "ma120") if mas.get(k) is not None]
    if len(values) < 3:
        return "판단불가"
    if all(a > b for a, b in zip(values, values[1:])):
        return "정배열"
    if all(a < b for a, b in zip(values, values[1:])):
        return "역배열"
    return "혼조"


def volume_ratio(volume: pd.Series, window: int = 20) -> float | None:
    """당일 거래량 / 직전 window 일 평균."""
    if len(volume) <= window:
        return None
    base = volume.iloc[-window - 1 : -1].mean()
    return float(volume.iloc[-1] / base) if base > 0 else None


def levels(df: pd.DataFrame, mas: dict[str, float | None]) -> tuple[float | None, float | None]:
    """(지지선, 저항선): 전일까지의 고저점·이평선 후보 중 종가에 가장 가까운 아래/위 값.

    당일 봉은 제외한다 — 신고가 마감 종목의 당일 윗꼬리가 저항선으로 잡히지 않도록.
    """
    close = float(df["close"].iloc[-1])
    prior = df.iloc[:-1]
    supports = [prior["low"].iloc[-20:].min(), prior["low"].iloc[-60:].min(), mas.get("ma20"), mas.get("ma60")]
    resists = [prior["high"].iloc[-20:].max(), prior["high"].iloc[-60:].max(), prior["high"].iloc[-250:].max()]
    below = [float(x) for x in supports if x is not None and x < close * 0.995]
    above = [float(x) for x in resists if x is not None and x > close * 1.005]
    return (max(below) if below else None), (min(above) if above else None)


def stop_level(close: float, support: float | None, atr_value: float | None, max_stop_pct: float = 12.0) -> int:
    """리스크 기준선(손절 참고가): 지지선 −0.5ATR, 지지선이 없으면 종가 −2ATR. 최대 −max_stop_pct%."""
    atr_value = atr_value or close * 0.03
    raw = support - 0.5 * atr_value if support else close - 2 * atr_value
    raw = max(raw, close * (1 - max_stop_pct / 100))
    return round_tick(min(raw, close - tick_size(close)), "down")


def quant_band(close: float, atr_value: float | None, k: float = 1.0) -> tuple[int, int]:
    """다음 거래일 정량 참고 밴드: 종가 ± k·ATR."""
    atr_value = atr_value or close * 0.03
    return round_tick(close - k * atr_value, "down"), round_tick(close + k * atr_value, "up")


def is_pullback(df: pd.DataFrame, mas: dict[str, float | None]) -> bool:
    """눌림목: 중기 상승 추세(20일선>60일선, 종가>60일선)에서 20일선 ±3%까지 조정 + 거래량 감소."""
    ma20, ma60 = mas.get("ma20"), mas.get("ma60")
    if ma20 is None or ma60 is None or len(df) < 25:
        return False
    close = float(df["close"].iloc[-1])
    recent_return = close / float(df["close"].iloc[-6]) - 1
    vol5 = df["volume"].iloc[-5:].mean()
    vol20 = df["volume"].iloc[-20:].mean()
    return (
        ma20 > ma60
        and close > ma60
        and abs(close / ma20 - 1) <= 0.03
        and recent_return < 0
        and vol5 < vol20
    )


def streak(values: list[float]) -> int:
    """최근 값부터 연속 양수(순매수) 일수. values 는 최근 → 과거 순."""
    n = 0
    for v in values:
        if v > 0:
            n += 1
        else:
            break
    return n


def analyze(df: pd.DataFrame, rsi_period: int = 14, max_stop_pct: float = 12.0) -> dict:
    """일봉 → 기술 지표 묶음."""
    close_s = df["close"]
    close = float(close_s.iloc[-1])
    mas = moving_averages(close_s)
    atr_value = atr(df)
    support, resistance = levels(df, mas)
    window = df.iloc[-250:]
    high_52w = float(window["high"].max())
    low_52w = float(window["low"].min())
    band_low, band_high = quant_band(close, atr_value)
    prev_close = float(close_s.iloc[-2]) if len(close_s) > 1 else close
    return {
        "date": str(df["date"].iloc[-1]),
        "close": close,
        "prev_close": prev_close,
        **mas,
        "ma_state": ma_state(mas),
        "rsi": rsi(close_s, rsi_period),
        "atr": atr_value,
        "volume_ratio": volume_ratio(df["volume"]),
        "high_52w": high_52w,
        "low_52w": low_52w,
        "pct_from_high": (close / high_52w - 1) * 100 if high_52w else None,
        "new_high": float(df["high"].iloc[-1]) >= high_52w,
        "pullback": is_pullback(df, mas),
        "support": round_tick(support, "down") if support else None,
        "resistance": round_tick(resistance, "up") if resistance else None,
        "stop": stop_level(close, support, atr_value, max_stop_pct),
        "band_low": band_low,
        "band_high": band_high,
    }
