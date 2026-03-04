"""
EnergyPreprocessor
------------------
Feature engineering pipeline for GreenPulse ML models with full data quality
cleaning: deduplication, outlier capping, negative clipping, zone normalisation,
and adaptive contamination estimation.

Features produced
-----------------
  Time    : hour, minute_bucket, day_of_week, month, quarter,
            day_of_year, week_of_year, is_weekend, is_business_hours,
            is_peak, is_night
  Rolling : mean/std/max over 1h, 6h, 24h windows; mean over 7d
  Lag     : 1h, 2h, 6h, 24h, 168h (one week)
  Zone    : label-encoded integer
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import LabelEncoder, StandardScaler

logger = logging.getLogger("greenpulse.ml.pipeline")

# ---------------------------------------------------------------------------
# Feature column lists
# ---------------------------------------------------------------------------
FORECAST_COLS = [
    "hour", "minute_bucket", "day_of_week", "month", "quarter",
    "day_of_year", "week_of_year",
    "is_weekend", "is_business_hours", "is_peak", "is_night",
    "rolling_mean_1h", "rolling_std_1h",
    "rolling_mean_6h",
    "rolling_mean_24h", "rolling_std_24h", "rolling_max_24h",
    "rolling_mean_7d",
    "lag_1h", "lag_2h", "lag_6h", "lag_24h", "lag_168h",
    "zone_encoded",
]

ANOMALY_COLS = [
    "hour", "day_of_week", "is_weekend", "is_business_hours", "is_peak", "is_night",
    "rolling_mean_24h", "rolling_std_24h",
    "lag_1h", "lag_24h",
    "zone_encoded",
    "consumption_kwh",
]

# Threshold for Modified Z-score (Iglewicz & Hoaglin 1993)
_MODIFIED_Z_THRESHOLD = 3.5

# IQR fence multiplier for outlier detection
_IQR_FENCE_MULT = 3.0


# ---------------------------------------------------------------------------
# Data quality report
# ---------------------------------------------------------------------------
@dataclass
class DataQualityReport:
    """Summary of cleaning operations applied to the input dataset."""
    duplicates_removed: int = 0
    negatives_clipped: int = 0
    outliers_capped: int = 0
    zero_readings: int = 0
    zones_normalised: int = 0
    estimated_contamination: float = 0.05
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "duplicates_removed": self.duplicates_removed,
            "negatives_clipped": self.negatives_clipped,
            "outliers_capped": self.outliers_capped,
            "zero_readings": self.zero_readings,
            "zones_normalised": self.zones_normalised,
            "estimated_contamination": round(self.estimated_contamination, 4),
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Preprocessor
# ---------------------------------------------------------------------------
class EnergyPreprocessor:
    """Stateful preprocessor -- fit on training data, transform at inference."""

    def __init__(self) -> None:
        self.forecast_scaler = StandardScaler()
        self.anomaly_scaler = StandardScaler()
        self.imputer = SimpleImputer(strategy="median")
        self.zone_encoder = LabelEncoder()
        self._zone_classes: list = []
        self.fitted = False
        self.quality_report: Optional[DataQualityReport] = None
        self.estimated_contamination: float = 0.05

    # -----------------------------------------------------------------------
    # Public builders (cleaned)
    # -----------------------------------------------------------------------

    def clean_orm(self, readings) -> pd.DataFrame:
        """Convert SQLAlchemy EnergyReading objects to a cleaned, enriched DataFrame."""
        if not readings:
            return pd.DataFrame()
        rows = [
            {
                "timestamp": pd.Timestamp(r.timestamp).tz_localize(None)
                if getattr(r.timestamp, "tzinfo", None)
                else pd.Timestamp(r.timestamp),
                "consumption_kwh": float(r.consumption_kwh),
                "zone": str(r.zone),
            }
            for r in readings
        ]
        df = pd.DataFrame(rows)
        df = self._clean(df)
        return self._engineer(df)

    def clean_dicts(self, records: List[dict]) -> pd.DataFrame:
        """Convert list of dicts (IoT / API push) to a cleaned, enriched DataFrame."""
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        df["consumption_kwh"] = df["consumption_kwh"].astype(float)
        if "zone" not in df.columns:
            df["zone"] = "main"
        df["zone"] = df["zone"].astype(str)
        df = self._clean(df)
        return self._engineer(df)

    # Backward-compatibility aliases (do not add cleaning to these)
    def from_orm(self, readings) -> pd.DataFrame:
        return self.clean_orm(readings)

    def from_dicts(self, records: List[dict]) -> pd.DataFrame:
        return self.clean_dicts(records)

    def future_frame(
        self,
        horizon_hours: int = 168,
        last_df: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        Build a feature DataFrame for the next horizon_hours timestamps.
        Lag values are filled from the tail of last_df (historical data).
        """
        now = pd.Timestamp.utcnow().replace(tzinfo=None, minute=0, second=0, microsecond=0)
        times = pd.date_range(now, periods=horizon_hours, freq="1h")

        df = pd.DataFrame({
            "timestamp": times,
            "consumption_kwh": 0.0,
            "zone": "main",
        })
        df = self._engineer(df)

        if last_df is not None and len(last_df):
            last_vals = last_df["consumption_kwh"].values

            def _last(n: int) -> float:
                return float(last_vals[-n]) if len(last_vals) >= n else float(last_vals.mean())

            df["lag_1h"]   = _last(1)
            df["lag_2h"]   = _last(2)
            df["lag_6h"]   = _last(6)
            df["lag_24h"]  = _last(24)
            df["lag_168h"] = _last(168)

            df["rolling_mean_1h"]  = float(last_vals[-1:].mean())
            df["rolling_std_1h"]   = 0.0
            df["rolling_mean_6h"]  = float(last_vals[-6:].mean()) if len(last_vals) >= 6 else float(last_vals.mean())
            df["rolling_mean_24h"] = float(last_vals[-24:].mean()) if len(last_vals) >= 24 else float(last_vals.mean())
            df["rolling_std_24h"]  = float(last_vals[-24:].std()) if len(last_vals) >= 24 else 0.0
            df["rolling_max_24h"]  = float(last_vals[-24:].max()) if len(last_vals) >= 24 else float(last_vals.max())
            df["rolling_mean_7d"]  = float(last_vals[-168:].mean()) if len(last_vals) >= 168 else float(last_vals.mean())

        return df

    # -----------------------------------------------------------------------
    # Transformers
    # -----------------------------------------------------------------------

    def forecast_X(self, df: pd.DataFrame, fit: bool = False) -> np.ndarray:
        cols = [c for c in FORECAST_COLS if c in df.columns]
        X = df[cols].values.astype(float)
        X = np.where(np.isfinite(X), X, 0.0)
        if fit:
            X = self.imputer.fit_transform(X)
            X = self.forecast_scaler.fit_transform(X)
            self.fitted = True
        else:
            X = self.imputer.transform(X)
            X = self.forecast_scaler.transform(X)
        return X

    def anomaly_X(self, df: pd.DataFrame, fit: bool = False) -> np.ndarray:
        cols = [c for c in ANOMALY_COLS if c in df.columns]
        X = df[cols].values.astype(float)
        X = np.where(np.isfinite(X), X, 0.0)
        if fit:
            X = self.anomaly_scaler.fit_transform(X)
        else:
            X = self.anomaly_scaler.transform(X)
        return X

    # -----------------------------------------------------------------------
    # Internal -- data cleaning
    # -----------------------------------------------------------------------

    def _clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Full cleaning pipeline applied before feature engineering.

        Steps (in order):
          1. Sort by timestamp
          2. Normalise zone names (strip, lowercase)
          3. Remove duplicate timestamps within the same zone
          4. Clip negative kWh values to zero
          5. Cap per-zone outliers using IQR fence
          6. Guard against NaN / Inf
          7. Flag zero readings (meter offline / gap)
          8. Estimate contamination for IsolationForest
        """
        report = DataQualityReport()
        n_start = len(df)

        # 1. Sort
        df = df.sort_values("timestamp").reset_index(drop=True)

        # 2. Normalise zone names
        orig_zones = df["zone"].copy()
        df["zone"] = df["zone"].str.strip().str.lower()
        report.zones_normalised = int((df["zone"] != orig_zones).sum())

        # 3. Deduplicate (keep last reading per zone+timestamp)
        before_dedup = len(df)
        df = df.drop_duplicates(subset=["timestamp", "zone"], keep="last")
        df = df.reset_index(drop=True)
        report.duplicates_removed = before_dedup - len(df)

        # 4. Clip negatives
        neg_mask = df["consumption_kwh"] < 0
        report.negatives_clipped = int(neg_mask.sum())
        df.loc[neg_mask, "consumption_kwh"] = 0.0

        # 5. Cap outliers per zone using IQR fence (Q3 + fence * IQR)
        total_capped = 0
        for zone in df["zone"].unique():
            mask = df["zone"] == zone
            vals = df.loc[mask, "consumption_kwh"]
            q1 = vals.quantile(0.25)
            q3 = vals.quantile(0.75)
            iqr = q3 - q1
            if iqr > 0:
                upper_fence = q3 + _IQR_FENCE_MULT * iqr
                over_mask = mask & (df["consumption_kwh"] > upper_fence)
                capped = int(over_mask.sum())
                if capped:
                    df.loc[over_mask, "consumption_kwh"] = upper_fence
                    total_capped += capped
        report.outliers_capped = total_capped

        # 6. NaN / Inf guard
        df["consumption_kwh"] = df["consumption_kwh"].replace([np.inf, -np.inf], np.nan)
        if df["consumption_kwh"].isna().any():
            median_val = df["consumption_kwh"].median()
            df["consumption_kwh"] = df["consumption_kwh"].fillna(median_val if not np.isnan(median_val) else 0.0)

        # 7. Count zero readings (potential meter-offline gaps)
        report.zero_readings = int((df["consumption_kwh"] == 0).sum())

        # 8. Dataset size warning
        n_final = len(df)
        if n_final < 50:
            report.warnings.append(
                f"Very small dataset ({n_final} rows after cleaning). "
                "Model accuracy will be limited."
            )
        if report.duplicates_removed > 0:
            report.warnings.append(
                f"Removed {report.duplicates_removed} duplicate timestamp/zone pairs."
            )
        if report.negatives_clipped > 0:
            report.warnings.append(
                f"Clipped {report.negatives_clipped} negative kWh reading(s) to zero."
            )
        if report.outliers_capped > 0:
            pct = round(report.outliers_capped / n_start * 100, 1)
            report.warnings.append(
                f"Capped {report.outliers_capped} extreme outlier(s) ({pct}% of input)."
            )

        # 9. Estimate contamination for IsolationForest
        report.estimated_contamination = self._estimate_contamination(
            df["consumption_kwh"].values
        )
        self.estimated_contamination = report.estimated_contamination
        self.quality_report = report

        if report.warnings:
            for w in report.warnings:
                logger.warning("[DataQuality] %s", w)

        logger.info(
            "[DataQuality] in=%d out=%d | dupes=%d neg=%d capped=%d zeros=%d contamination=%.3f",
            n_start, n_final,
            report.duplicates_removed, report.negatives_clipped,
            report.outliers_capped, report.zero_readings,
            report.estimated_contamination,
        )

        return df

    def _estimate_contamination(self, values: np.ndarray) -> float:
        """
        Dual-method contamination estimate for IsolationForest:
          Method A: Modified Z-score (Iglewicz & Hoaglin, threshold=3.5)
          Method B: IQR fence (Q3 + 3*IQR)
        Average the two fractions and clamp to [0.01, 0.20].
        """
        if len(values) < 4:
            return 0.05

        # Method A: Modified Z-score (MAD-based, robust to the outliers themselves)
        median = np.median(values)
        mad = np.median(np.abs(values - median))
        if mad == 0:
            frac_a = 0.0
        else:
            modified_z = 0.6745 * np.abs(values - median) / mad
            frac_a = float(np.mean(modified_z > _MODIFIED_Z_THRESHOLD))

        # Method B: IQR fence
        q1 = np.percentile(values, 25)
        q3 = np.percentile(values, 75)
        iqr = q3 - q1
        if iqr == 0:
            frac_b = 0.0
        else:
            upper_fence = q3 + _IQR_FENCE_MULT * iqr
            frac_b = float(np.mean(values > upper_fence))

        avg_frac = (frac_a + frac_b) / 2.0
        return float(np.clip(avg_frac, 0.01, 0.20))

    # -----------------------------------------------------------------------
    # Internal -- feature engineering
    # -----------------------------------------------------------------------

    def _engineer(self, df: pd.DataFrame) -> pd.DataFrame:
        ts = df["timestamp"]

        df["hour"]              = ts.dt.hour
        df["minute_bucket"]     = (ts.dt.minute // 15).astype(int)
        df["day_of_week"]       = ts.dt.dayofweek
        df["month"]             = ts.dt.month
        df["quarter"]           = ts.dt.quarter
        df["day_of_year"]       = ts.dt.dayofyear
        df["week_of_year"]      = ts.dt.isocalendar().week.astype(int)
        df["is_weekend"]        = (df["day_of_week"] >= 5).astype(int)
        df["is_business_hours"] = ((df["hour"] >= 8) & (df["hour"] <= 18)).astype(int)
        df["is_peak"]           = ((df["hour"] >= 16) & (df["hour"] <= 19)).astype(int)
        df["is_night"]          = ((df["hour"] <= 5) | (df["hour"] >= 22)).astype(int)

        # Rolling features
        df = df.set_index("timestamp")
        kw = df["consumption_kwh"]
        df["rolling_mean_1h"]  = kw.rolling("1h",  min_periods=1).mean()
        df["rolling_std_1h"]   = kw.rolling("1h",  min_periods=1).std().fillna(0)
        df["rolling_mean_6h"]  = kw.rolling("6h",  min_periods=1).mean()
        df["rolling_mean_24h"] = kw.rolling("24h", min_periods=1).mean()
        df["rolling_std_24h"]  = kw.rolling("24h", min_periods=1).std().fillna(0)
        df["rolling_max_24h"]  = kw.rolling("24h", min_periods=1).max()
        df["rolling_mean_7d"]  = kw.rolling("7D",  min_periods=1).mean()
        df = df.reset_index()

        # Lag features
        mean_val = df["consumption_kwh"].mean()
        df["lag_1h"]   = df["consumption_kwh"].shift(1).fillna(mean_val)
        df["lag_2h"]   = df["consumption_kwh"].shift(2).fillna(mean_val)
        df["lag_6h"]   = df["consumption_kwh"].shift(6).fillna(mean_val)
        df["lag_24h"]  = df["consumption_kwh"].shift(24).fillna(mean_val)
        df["lag_168h"] = df["consumption_kwh"].shift(168).fillna(mean_val)

        # Zone encoding
        if not self._zone_classes:
            self.zone_encoder.fit(df["zone"])
            self._zone_classes = list(self.zone_encoder.classes_)
        known = set(self._zone_classes)
        df["_zone_safe"] = df["zone"].apply(
            lambda z: z if z in known else self._zone_classes[0]
        )
        df["zone_encoded"] = self.zone_encoder.transform(df["_zone_safe"])
        df.drop(columns=["_zone_safe"], inplace=True)

        return df
