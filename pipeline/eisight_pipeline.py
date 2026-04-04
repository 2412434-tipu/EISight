
"""
=============================================================================
EISight: Reviewed ML Pipeline for EIS Food Adulteration Detection
=============================================================================
Project     : AURA - IUB
Team        : EISight (Tipu Sultan, Sajidur Rahman Mahin, Mir Erfan Kabir Rafi)
Supervisor  : Dr. Feroz Ahmed

What this script is for
-----------------------
This is the CANONICAL simulation / training / evaluation pipeline for EISight.

Use this file for:
  1) Generating synthetic Cole-Cole spectra
  2) Extracting reproducible features
  3) Training and evaluating ML models
  4) Exporting a LOCAL PYTHON inference bundle for Streamlit/demo use

What this script is NOT for
---------------------------
This is still a simulation-grade / early real-data pipeline.
It is NOT yet a full instrument-grade AD5933 calibration stack.

When real hardware data arrives, you will still need:
  - per-session resistor calibration
  - trusted-band selection
  - empirical temperature correction
  - day-grouped validation
  - sweep rejection QC
  - same-day control normalization

Usage
-----
  python eisight_pipeline_reviewed.py --simulate
  python eisight_pipeline_reviewed.py --data path/to/real_sweeps.csv
  python eisight_pipeline_reviewed.py --data path/to/real_longformat.csv --input-format long
=============================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import warnings
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ============================================================================
# CONFIGURATION
# ============================================================================
@dataclass
class Config:
    PROJECT_NAME: str = "EISight"
    VERSION: str = "1.1.2-reviewed-patched"
    OUTPUT_DIR: str = "pipeline_outputs"

    # Frequency grid used by the simulator and assumed by the wide-format loader.
    FREQ_START_HZ: int = 1_000
    FREQ_END_HZ: int = 100_000
    FREQ_POINTS: int = 100
    ANCHOR_FREQS_HZ: Tuple[int, ...] = (5_000, 10_000, 25_000, 50_000, 80_000)

    # Cole-Cole baseline parameters used ONLY for synthetic-data generation.
    PURE_MILK: Dict[str, float] = None  # type: ignore[assignment]
    NOISE_STD: float = 2.5

    # ML / CV
    RANDOM_STATE: int = 42
    CV_FOLDS: int = 5

    # Verdict thresholds are applied to the BINARY model probability of PURE / PASS.
    CONFIDENCE_PASS: float = 0.85
    CONFIDENCE_WARN: float = 0.60

    # Preprocessing flags
    APPLY_TEMP_NORMALIZATION: bool = True
    TEMP_NORMALIZATION_COEFF_PER_DEG_C: float = 0.015  # placeholder for simulation only
    SMOOTH_WINDOW: int = 5
    DROP_INITIAL_POINTS: int = 0  # for real AD5933 later, you may set this > 0
    MAX_ADJACENT_PHASE_JUMP_DEG: Optional[float] = None  # set later for hardware QC
    REQUIRE_POSITIVE_MAGNITUDE: bool = True

    # Simulation sample counts
    SIM_SAMPLES: Dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.PURE_MILK is None:
            self.PURE_MILK = {"R_0": 420.0, "R_inf": 75.0, "tau": 1.2e-5, "alpha": 0.78}
        if self.SIM_SAMPLES is None:
            self.SIM_SAMPLES = {
                "Pure": 70,
                "Water_10%": 50,
                "Water_20%": 50,
                "Water_30%": 50,
                "Water_50%": 50,
                "Urea_0.5%": 50,
                "Urea_1.0%": 50,
                "Soda_0.25%": 40,
                "Soda_0.5%": 40,
            }


cfg = Config()


# ============================================================================
# UTILITIES
# ============================================================================
def log(message: str, level: str = "INFO") -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def ensure_dirs(output_dir: str) -> None:
    for sub in ("", "figures", "models", "data", "reports"):
        os.makedirs(os.path.join(output_dir, sub), exist_ok=True)


def default_frequencies() -> np.ndarray:
    return np.linspace(cfg.FREQ_START_HZ, cfg.FREQ_END_HZ, cfg.FREQ_POINTS)


def ordered_columns(columns: Sequence[str], prefix: str) -> List[str]:
    def key_fn(name: str) -> int:
        return int(name.split("_")[-1])

    return sorted([c for c in columns if c.startswith(prefix)], key=key_fn)


def trapz_safe(y: np.ndarray, x: np.ndarray) -> float:
    """Compatibility wrapper for NumPy 1.x and 2.x."""
    try:
        return float(np.trapezoid(y=y, x=x))
    except AttributeError:
        return float(np.trapz(y=y, x=x))


def to_numpy_str_array(values: Sequence[object]) -> np.ndarray:
    """Return a plain NumPy string/object array safe for scikit-learn indexing.

    This avoids pandas ArrowStringArray / pyarrow-backed string arrays leaking into
    scikit-learn cross-validation helpers, which can trigger indexing errors on some
    local setups.
    """
    if isinstance(values, pd.Series):
        ser = values.copy()
    else:
        ser = pd.Series(values, copy=False)
    ser = ser.fillna("").astype(str)
    return ser.to_numpy(dtype=object)


def to_numpy_group_array(values: Optional[Sequence[object]]) -> Optional[np.ndarray]:
    if values is None:
        return None
    if isinstance(values, pd.Series):
        ser = values.copy()
    else:
        ser = pd.Series(values, copy=False)
    ser = ser.fillna("missing").astype(str)
    return ser.to_numpy(dtype=object)


def detect_input_format(filepath: str) -> str:
    preview = pd.read_csv(filepath, nrows=5)
    cols = set(preview.columns)

    long_required = {"sweep_id", "label", "frequency_hz", "Z_real", "Z_imag", "temperature_C"}
    wide_required = {"sample_id", "label", "temperature_C"}

    if long_required.issubset(cols):
        return "long"

    mag_cols = [c for c in preview.columns if c.startswith("Z_mag_")]
    phase_cols = [c for c in preview.columns if c.startswith("Z_phase_")]

    if wide_required.issubset(cols) and mag_cols and phase_cols:
        return "wide"

    raise ValueError(
        "Could not detect input format. Expected either:\n"
        "  1) wide sweep format with sample_id, label, temperature_C, Z_mag_0.., Z_phase_0..\n"
        "  2) long format with sweep_id, label, frequency_hz, Z_real, Z_imag, temperature_C"
    )


# ============================================================================
# STAGE 1: DATA GENERATION / LOADING
# ============================================================================
class DataGenerator:
    def __init__(self, frequencies: Optional[np.ndarray] = None, random_state: int = cfg.RANDOM_STATE):
        self.frequencies = default_frequencies() if frequencies is None else np.asarray(frequencies, dtype=float)
        self.omega = 2.0 * np.pi * self.frequencies
        self.rng = np.random.default_rng(random_state)

    def cole_cole(
        self,
        omega: np.ndarray,
        R_0: float,
        R_inf: float,
        tau: float,
        alpha: float,
        noise_std: float = 0.0,
    ) -> np.ndarray:
        Z = R_inf + (R_0 - R_inf) / (1.0 + (1j * omega * tau) ** alpha)
        if noise_std > 0:
            Z = Z + (
                self.rng.normal(0.0, noise_std, size=Z.shape)
                + 1j * self.rng.normal(0.0, noise_std, size=Z.shape)
            )
        return Z

    def adulterant_params(self, adulterant: str, concentration: float) -> Dict[str, float]:
        p = dict(cfg.PURE_MILK)

        if adulterant == "water":
            f = concentration / 100.0
            p["R_0"] *= 1.0 + 1.8 * f
            p["R_inf"] *= 1.0 + 0.6 * f
            p["tau"] *= 1.0 + 0.8 * f
            p["alpha"] -= 0.05 * f
        elif adulterant == "urea":
            f = concentration / 1.0
            p["R_0"] *= 1.0 - 0.25 * f
            p["R_inf"] *= 1.0 - 0.15 * f
            p["tau"] *= 1.0 - 0.12 * f
            p["alpha"] += 0.03 * f
        elif adulterant == "baking_soda":
            f = concentration / 0.5
            p["R_0"] *= 1.0 - 0.35 * f
            p["R_inf"] *= 1.0 - 0.25 * f
            p["tau"] *= 1.0 - 0.15 * f
            p["alpha"] += 0.05 * f
        else:
            raise ValueError(f"Unsupported adulterant type: {adulterant}")

        return p

    def temperature_effect(self, params: Dict[str, float], temp_c: float) -> Dict[str, float]:
        delta = temp_c - 25.0
        factor = 1.0 - cfg.TEMP_NORMALIZATION_COEFF_PER_DEG_C * delta
        adjusted = dict(params)
        adjusted["R_0"] *= factor
        adjusted["R_inf"] *= factor
        adjusted["tau"] *= 1.0 - 0.008 * delta
        return adjusted

    def generate_dataset(self) -> Tuple[pd.DataFrame, List[Dict[str, np.ndarray]], np.ndarray]:
        log("Generating simulated dataset (Cole-Cole model)...")

        configs = [
            (None, 0.0, "Pure"),
            ("water", 10.0, "Water_10%"),
            ("water", 20.0, "Water_20%"),
            ("water", 30.0, "Water_30%"),
            ("water", 50.0, "Water_50%"),
            ("urea", 0.5, "Urea_0.5%"),
            ("urea", 1.0, "Urea_1.0%"),
            ("baking_soda", 0.25, "Soda_0.25%"),
            ("baking_soda", 0.5, "Soda_0.5%"),
        ]

        records: List[Dict[str, object]] = []
        raw_spectra: List[Dict[str, np.ndarray]] = []

        for adulterant, conc, label in configs:
            n_samples = cfg.SIM_SAMPLES[label]
            for i in range(n_samples):
                temp_c = float(self.rng.uniform(22.0, 28.0))
                if adulterant is None:
                    params = self.temperature_effect(cfg.PURE_MILK, temp_c)
                else:
                    params = self.temperature_effect(self.adulterant_params(adulterant, conc), temp_c)

                params["R_0"] *= float(self.rng.uniform(0.97, 1.03))
                params["R_inf"] *= float(self.rng.uniform(0.97, 1.03))
                params["tau"] *= float(self.rng.uniform(0.98, 1.02))

                Z = self.cole_cole(self.omega, **params, noise_std=cfg.NOISE_STD)
                Z_mag = np.abs(Z)
                Z_phase = np.degrees(np.angle(Z))

                row: Dict[str, object] = {
                    "sample_id": f"{label}_{i:03d}",
                    "label": label,
                    "binary_label": "PASS" if label == "Pure" else "FAIL",
                    "temperature_C": round(temp_c, 2),
                    "data_source": "simulation",
                }

                for j in range(len(self.frequencies)):
                    row[f"Z_mag_{j}"] = float(np.round(Z_mag[j], 4))
                    row[f"Z_phase_{j}"] = float(np.round(Z_phase[j], 4))

                records.append(row)

                if i == 0:
                    raw_spectra.append(
                        {
                            "label": label,
                            "Z_mag": Z_mag,
                            "Z_phase": Z_phase,
                            "Z_real": Z.real,
                            "Z_imag": Z.imag,
                        }
                    )

        df = pd.DataFrame(records)
        log(f"  Generated {len(df)} samples across {df['label'].nunique()} classes")
        return df, raw_spectra, self.frequencies


class DataLoader:
    @staticmethod
    def load_wide(filepath: str) -> Tuple[pd.DataFrame, np.ndarray]:
        log(f"Loading wide-format data from {filepath} ...")
        df = pd.read_csv(filepath)

        required = {"sample_id", "label", "temperature_C"}
        if not required.issubset(df.columns):
            raise ValueError(f"Wide-format CSV is missing required columns: {sorted(required - set(df.columns))}")

        mag_cols = ordered_columns(df.columns, "Z_mag_")
        phase_cols = ordered_columns(df.columns, "Z_phase_")
        if not mag_cols or not phase_cols:
            raise ValueError("Wide-format CSV must contain Z_mag_0.. and Z_phase_0.. columns")

        if "binary_label" not in df.columns:
            df["binary_label"] = df["label"].astype(str).str.lower().map(lambda x: "PASS" if "pure" in x else "FAIL")

        if "data_source" not in df.columns:
            df["data_source"] = "real_ad5933"

        frequencies = np.linspace(cfg.FREQ_START_HZ, cfg.FREQ_END_HZ, len(mag_cols))
        log(f"  Loaded {len(df)} sweeps with {len(mag_cols)} frequency points each")
        return df, frequencies

    @staticmethod
    def load_long(filepath: str) -> Tuple[pd.DataFrame, np.ndarray]:
        log(f"Loading long-format data from {filepath} ...")
        raw = pd.read_csv(filepath)

        required = {"sweep_id", "label", "frequency_hz", "Z_real", "Z_imag", "temperature_C"}
        if not required.issubset(raw.columns):
            raise ValueError(f"Long-format CSV is missing required columns: {sorted(required - set(raw.columns))}")

        raw = raw.copy()
        raw["Z_mag"] = np.sqrt(raw["Z_real"] ** 2 + raw["Z_imag"] ** 2)
        raw["Z_phase"] = np.degrees(np.arctan2(raw["Z_imag"], raw["Z_real"]))

        frequencies = np.array(sorted(raw["frequency_hz"].unique()), dtype=float)

        sweeps = (
            raw.groupby("sweep_id", as_index=False)
            .first()[["sweep_id", "label", "temperature_C"]]
            .rename(columns={"sweep_id": "sample_id"})
        )

        if "day" in raw.columns:
            day_df = raw.groupby("sweep_id", as_index=False).first()[["sweep_id", "day"]]
            day_df = day_df.rename(columns={"sweep_id": "sample_id"})
            sweeps = sweeps.merge(day_df, on="sample_id", how="left")

        for i, freq in enumerate(frequencies):
            freq_rows = raw.loc[raw["frequency_hz"] == freq, ["sweep_id", "Z_mag", "Z_phase"]].rename(
                columns={"sweep_id": "sample_id", "Z_mag": f"Z_mag_{i}", "Z_phase": f"Z_phase_{i}"}
            )
            sweeps = sweeps.merge(freq_rows, on="sample_id", how="left")

        sweeps["binary_label"] = sweeps["label"].astype(str).str.lower().map(lambda x: "PASS" if "pure" in x else "FAIL")
        sweeps["data_source"] = "real_ad5933"

        log(f"  Loaded {len(sweeps)} sweeps with {len(frequencies)} frequency points each")
        return sweeps, frequencies


# ============================================================================
# STAGE 2: PREPROCESSING
# ============================================================================
class Preprocessor:
    """
    NOTE:
      - This class is safe for simulation and preliminary real-data work.
      - It is NOT a substitute for true AD5933 calibration.
    """

    def __init__(
        self,
        frequencies: np.ndarray,
        apply_temp_normalization: bool = cfg.APPLY_TEMP_NORMALIZATION,
        temp_coeff_per_deg_c: float = cfg.TEMP_NORMALIZATION_COEFF_PER_DEG_C,
        smooth_window: int = cfg.SMOOTH_WINDOW,
        drop_initial_points: int = cfg.DROP_INITIAL_POINTS,
        max_adjacent_phase_jump_deg: Optional[float] = cfg.MAX_ADJACENT_PHASE_JUMP_DEG,
        require_positive_magnitude: bool = cfg.REQUIRE_POSITIVE_MAGNITUDE,
    ) -> None:
        self.frequencies = np.asarray(frequencies, dtype=float)
        self.apply_temp_normalization = apply_temp_normalization
        self.temp_coeff_per_deg_c = temp_coeff_per_deg_c
        self.smooth_window = smooth_window
        self.drop_initial_points = int(drop_initial_points)
        self.max_adjacent_phase_jump_deg = max_adjacent_phase_jump_deg
        self.require_positive_magnitude = require_positive_magnitude

    def _drop_invalid_rows(self, df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
        mag_cols = ordered_columns(df.columns, "Z_mag_")
        phase_cols = ordered_columns(df.columns, "Z_phase_")
        keep_mask = np.ones(len(df), dtype=bool)

        mag = df[mag_cols].to_numpy(dtype=float)
        phase = df[phase_cols].to_numpy(dtype=float)

        keep_mask &= np.isfinite(mag).all(axis=1)
        keep_mask &= np.isfinite(phase).all(axis=1)

        if self.require_positive_magnitude:
            keep_mask &= (mag > 0).all(axis=1)

        if self.max_adjacent_phase_jump_deg is not None and phase.shape[1] > 1:
            phase_jump = np.abs(np.diff(phase, axis=1))
            keep_mask &= (phase_jump <= self.max_adjacent_phase_jump_deg).all(axis=1)

        removed = int((~keep_mask).sum())
        if removed and verbose:
            log(f"  Dropped {removed} invalid sweeps during basic QC")

        return df.loc[keep_mask].reset_index(drop=True)

    def _drop_initial_frequency_points(self, df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
        if self.drop_initial_points <= 0:
            return df

        mag_cols = ordered_columns(df.columns, "Z_mag_")
        phase_cols = ordered_columns(df.columns, "Z_phase_")

        if self.drop_initial_points >= len(mag_cols):
            raise ValueError("drop_initial_points is larger than or equal to the number of sweep points")

        keep_mag = mag_cols[self.drop_initial_points :]
        keep_phase = phase_cols[self.drop_initial_points :]

        meta_cols = [c for c in df.columns if not (c.startswith("Z_mag_") or c.startswith("Z_phase_"))]
        trimmed = df[meta_cols + keep_mag + keep_phase].copy()

        renames: Dict[str, str] = {}
        for new_idx, old_name in enumerate(keep_mag):
            renames[old_name] = f"Z_mag_{new_idx}"
        for new_idx, old_name in enumerate(keep_phase):
            renames[old_name] = f"Z_phase_{new_idx}"

        trimmed = trimmed.rename(columns=renames)
        self.frequencies = self.frequencies[self.drop_initial_points :]
        if verbose:
            log(f"  Dropped first {self.drop_initial_points} frequency points")
        return trimmed

    def _temperature_normalize(self, df: pd.DataFrame, ref_temp_c: float = 25.0, verbose: bool = True) -> pd.DataFrame:
        if not self.apply_temp_normalization or "temperature_C" not in df.columns:
            return df

        mag_cols = ordered_columns(df.columns, "Z_mag_")
        correction = 1.0 + self.temp_coeff_per_deg_c * (df["temperature_C"].astype(float) - ref_temp_c)
        df = df.copy()
        df.loc[:, mag_cols] = df.loc[:, mag_cols].mul(correction, axis=0)
        if verbose:
            log(f"  Applied placeholder temperature normalization to {ref_temp_c:.1f}°C")
        return df

    def _smooth_spectra(self, df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
        if self.smooth_window <= 1:
            return df

        mag_cols = ordered_columns(df.columns, "Z_mag_")
        phase_cols = ordered_columns(df.columns, "Z_phase_")

        df = df.copy()
        df.loc[:, mag_cols] = df.loc[:, mag_cols].T.rolling(
            window=self.smooth_window, center=True, min_periods=1
        ).mean().T
        df.loc[:, phase_cols] = df.loc[:, phase_cols].T.rolling(
            window=self.smooth_window, center=True, min_periods=1
        ).mean().T

        if verbose:
            log(f"  Applied moving-average smoothing with window={self.smooth_window}")
        return df

    def preprocess(self, df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
        if verbose:
            log("Stage 2: Preprocessing ...")
        out = df.copy()
        out = self._drop_invalid_rows(out, verbose=verbose)
        out = self._drop_initial_frequency_points(out, verbose=verbose)
        out = self._temperature_normalize(out, verbose=verbose)
        out = self._smooth_spectra(out, verbose=verbose)
        return out


# ============================================================================
# STAGE 3: FEATURE EXTRACTION
# ============================================================================
class FeatureExtractor:
    def __init__(self, frequencies: np.ndarray):
        self.frequencies = np.asarray(frequencies, dtype=float)
        self.anchor_indices = [int(np.argmin(np.abs(self.frequencies - f))) for f in cfg.ANCHOR_FREQS_HZ]
        self.feature_names: List[str] = []

    def extract(self, df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
        if verbose:
            log("Stage 3: Feature extraction ...")

        mag_cols = ordered_columns(df.columns, "Z_mag_")
        phase_cols = ordered_columns(df.columns, "Z_phase_")
        if not mag_cols or not phase_cols:
            raise ValueError("FeatureExtractor expects raw sweep columns Z_mag_i and Z_phase_i")

        features_list: List[Dict[str, float]] = []

        for _, row in df.iterrows():
            Z_mag = row[mag_cols].to_numpy(dtype=float)
            Z_phase = row[phase_cols].to_numpy(dtype=float)

            feat: Dict[str, float] = {}

            # 1) Anchor-frequency magnitude and phase
            for anchor_idx, freq_hz in zip(self.anchor_indices, cfg.ANCHOR_FREQS_HZ):
                freq_khz = int(round(freq_hz / 1e3))
                if anchor_idx < len(Z_mag):
                    feat[f"Z_mag_{freq_khz}kHz"] = float(Z_mag[anchor_idx])
                    feat[f"Z_phase_{freq_khz}kHz"] = float(Z_phase[anchor_idx])

            # 2) Ratios
            a0, a1, a2, a3, a4 = self.anchor_indices
            if max(self.anchor_indices) < len(Z_mag):
                feat["ratio_5_50kHz"] = float(Z_mag[a0] / (Z_mag[a3] + 1e-9))
                feat["ratio_10_80kHz"] = float(Z_mag[a1] / (Z_mag[a4] + 1e-9))
                feat["ratio_5_80kHz"] = float(Z_mag[a0] / (Z_mag[a4] + 1e-9))
                feat["phase_slope"] = float(
                    (Z_phase[a4] - Z_phase[a0]) / (cfg.ANCHOR_FREQS_HZ[4] - cfg.ANCHOR_FREQS_HZ[0])
                )

            # 3) Full-spectrum statistics
            feat["Z_mag_mean"] = float(np.mean(Z_mag))
            feat["Z_mag_std"] = float(np.std(Z_mag))
            feat["Z_mag_min"] = float(np.min(Z_mag))
            feat["Z_mag_max"] = float(np.max(Z_mag))
            feat["Z_mag_range"] = float(np.max(Z_mag) - np.min(Z_mag))
            feat["Z_phase_mean"] = float(np.mean(Z_phase))
            feat["Z_phase_std"] = float(np.std(Z_phase))

            # 4) Shape statistics
            s = pd.Series(Z_mag)
            feat["Z_mag_skewness"] = float(s.skew())
            feat["Z_mag_kurtosis"] = float(s.kurtosis())

            # 5) Approximate Nyquist area from mag + phase
            Z_real = Z_mag * np.cos(np.radians(Z_phase))
            Z_imag = Z_mag * np.sin(np.radians(Z_phase))
            feat["nyquist_area"] = abs(trapz_safe(-Z_imag, Z_real))

            # 6) Derivative statistics
            feat["mag_diff_mean"] = float(np.mean(np.diff(Z_mag)))
            feat["phase_diff_mean"] = float(np.mean(np.diff(Z_phase)))

            features_list.append(feat)

        features_df = pd.DataFrame(features_list)
        features_df = features_df.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        self.feature_names = list(features_df.columns)

        if verbose:
            log(f"  Extracted {len(self.feature_names)} features per sample")

        return features_df


# ============================================================================
# STAGE 4: MODEL TRAINING / VALIDATION
# ============================================================================
class MLTrainer:
    def __init__(self) -> None:
        self.results: Dict[str, Dict[str, Dict[str, object]]] = {}
        self.final_models: Dict[str, object] = {}
        self.best_multiclass_name: Optional[str] = None
        self.best_binary_name: Optional[str] = None

    def _build_cv(self, groups: Optional[Sequence[object]] = None):
        if groups is not None:
            groups = np.asarray(groups)
            unique_groups = np.unique(groups)
            if len(unique_groups) >= cfg.CV_FOLDS:
                try:
                    from sklearn.model_selection import StratifiedGroupKFold
                    return StratifiedGroupKFold(
                        n_splits=cfg.CV_FOLDS, shuffle=True, random_state=cfg.RANDOM_STATE
                    )
                except Exception:
                    from sklearn.model_selection import GroupKFold
                    return GroupKFold(n_splits=cfg.CV_FOLDS)
        from sklearn.model_selection import StratifiedKFold
        return StratifiedKFold(n_splits=cfg.CV_FOLDS, shuffle=True, random_state=cfg.RANDOM_STATE)

    def train_and_evaluate(
        self,
        X: pd.DataFrame,
        y_multi: Sequence[str],
        y_binary: Sequence[str],
        feature_names: Sequence[str],
        groups: Optional[Sequence[object]] = None,
    ) -> Dict[str, Dict[str, Dict[str, object]]]:
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.metrics import accuracy_score, f1_score
        from sklearn.model_selection import cross_val_predict
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.svm import SVC

        log("Stage 4: ML training and evaluation ...")

        # Force plain NumPy label/group arrays to avoid pandas ArrowArray indexing
        # issues inside scikit-learn cross-validation on some local environments.
        if not isinstance(X, pd.DataFrame):
            X = pd.DataFrame(np.asarray(X, dtype=float), columns=list(feature_names))
        else:
            X = X.copy()
            for col in X.columns:
                X[col] = pd.to_numeric(X[col], errors="coerce")
            X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)

        y_multi_arr = to_numpy_str_array(y_multi)
        y_binary_arr = to_numpy_str_array(y_binary)
        groups_arr = to_numpy_group_array(groups)

        cv = self._build_cv(groups_arr)
        use_grouped_cv = groups_arr is not None and len(np.unique(groups_arr)) >= cfg.CV_FOLDS

        multiclass_models = {
            "GradientBoosting": GradientBoostingClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.1, random_state=cfg.RANDOM_STATE
            ),
            "RandomForest": RandomForestClassifier(
                n_estimators=200, max_depth=10, random_state=cfg.RANDOM_STATE
            ),
            "SVM_RBF": SVC(kernel="rbf", C=10, gamma="scale", probability=True, random_state=cfg.RANDOM_STATE),
        }

        binary_models = {
            "GradientBoosting": GradientBoostingClassifier(
                n_estimators=150, max_depth=4, learning_rate=0.1, random_state=cfg.RANDOM_STATE
            ),
            "SVM_RBF": SVC(kernel="rbf", C=10, gamma="scale", probability=True, random_state=cfg.RANDOM_STATE),
        }

        self.results = {"multiclass": {}, "binary": {}}

        log("  Training multiclass models ...")
        for name, clf in multiclass_models.items():
            pipe = Pipeline([("scaler", StandardScaler()), ("clf", clf)])

            if use_grouped_cv:
                y_pred = cross_val_predict(pipe, X, y_multi_arr, cv=cv, groups=groups_arr)
            else:
                y_pred = cross_val_predict(pipe, X, y_multi_arr, cv=cv)

            f1 = float(f1_score(y_multi_arr, y_pred, average="weighted"))
            acc = float(accuracy_score(y_multi_arr, y_pred))

            pipe.fit(X, y_multi_arr)
            self.final_models[f"multi_{name}"] = pipe

            self.results["multiclass"][name] = {
                "f1_weighted": round(f1, 4),
                "accuracy": round(acc, 4),
                "y_true": np.asarray(y_multi_arr, dtype=object),
                "y_pred": y_pred,
            }
            log(f"    {name:20s} | F1={f1:.4f} | Acc={acc:.4f}")

        log("  Training binary models ...")
        for name, clf in binary_models.items():
            pipe = Pipeline([("scaler", StandardScaler()), ("clf", clf)])

            if use_grouped_cv:
                y_pred = cross_val_predict(pipe, X, y_binary_arr, cv=cv, groups=groups_arr)
            else:
                y_pred = cross_val_predict(pipe, X, y_binary_arr, cv=cv)

            f1 = float(f1_score(y_binary_arr, y_pred, average="weighted"))
            acc = float(accuracy_score(y_binary_arr, y_pred))

            pipe.fit(X, y_binary_arr)
            self.final_models[f"binary_{name}"] = pipe

            self.results["binary"][name] = {
                "f1_weighted": round(f1, 4),
                "accuracy": round(acc, 4),
                "y_true": np.asarray(y_binary_arr, dtype=object),
                "y_pred": y_pred,
            }
            log(f"    {name:20s} | F1={f1:.4f} | Acc={acc:.4f}")

        self.best_multiclass_name = max(
            self.results["multiclass"], key=lambda k: self.results["multiclass"][k]["f1_weighted"]
        )
        self.best_binary_name = max(
            self.results["binary"], key=lambda k: self.results["binary"][k]["f1_weighted"]
        )

        # Feature importance: prefer RandomForest because it always exposes tree importances
        # and gives a stable, interpretable ranking for the report, even if another model
        # slightly outperforms it on F1.
        preferred_fi_model = None
        for candidate in ("RandomForest", self.best_multiclass_name):
            if candidate is None:
                continue
            pipe = self.final_models.get(f"multi_{candidate}")
            if pipe is None:
                continue
            clf = pipe.named_steps["clf"]
            if hasattr(clf, "feature_importances_"):
                preferred_fi_model = clf
                break

        if preferred_fi_model is not None:
            self.results["feature_importance"] = dict(zip(feature_names, preferred_fi_model.feature_importances_))

        self.results["cv_type"] = {
            "name": type(cv).__name__,
            "grouped": bool(use_grouped_cv),
        }
        return self.results


# ============================================================================
# STAGE 5: EVALUATION / PLOTTING
# ============================================================================
class Evaluator:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir

    def plot_spectra(self, raw_spectra: List[Dict[str, np.ndarray]], frequencies: np.ndarray) -> str:
        log("  Generating spectra plots ...")
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle("EIS Response: Pure vs Adulterated Milk (Synthetic Cole-Cole Data)", fontsize=14, fontweight="bold")

        cmap = plt.cm.tab10
        for i, spec in enumerate(raw_spectra):
            color = cmap(i % 10)
            label = spec["label"]
            axes[0, 0].plot(frequencies / 1e3, spec["Z_mag"], color=color, lw=1.5, label=label)
            axes[0, 1].plot(spec["Z_real"], -spec["Z_imag"], color=color, lw=1.5, label=label)
            axes[1, 0].plot(frequencies / 1e3, spec["Z_phase"], color=color, lw=1.5, label=label)

        axes[0, 0].set_title("Bode Magnitude")
        axes[0, 0].set_xlabel("Frequency (kHz)")
        axes[0, 0].set_ylabel("|Z| (Ohm)")
        axes[0, 0].set_xscale("log")
        axes[0, 0].grid(alpha=0.3)
        axes[0, 0].legend(fontsize=7)

        axes[0, 1].set_title("Nyquist")
        axes[0, 1].set_xlabel("Z' (Ohm)")
        axes[0, 1].set_ylabel("-Z'' (Ohm)")
        axes[0, 1].grid(alpha=0.3)
        axes[0, 1].legend(fontsize=7)

        axes[1, 0].set_title("Bode Phase")
        axes[1, 0].set_xlabel("Frequency (kHz)")
        axes[1, 0].set_ylabel("Phase (deg)")
        axes[1, 0].set_xscale("log")
        axes[1, 0].grid(alpha=0.3)
        axes[1, 0].legend(fontsize=7)

        axes[1, 1].axis("off")
        axes[1, 1].text(
            0.5,
            0.5,
            "Synthetic feasibility study\n\n"
            f"Freq grid: {frequencies[0]/1e3:.1f} to {frequencies[-1]/1e3:.1f} kHz\n"
            f"Points: {len(frequencies)}\n"
            f"Noise: sigma={cfg.NOISE_STD:.1f} Ohm",
            ha="center",
            va="center",
            fontsize=11,
            bbox=dict(boxstyle="round", facecolor="lavender", alpha=0.35),
        )

        plt.tight_layout()
        out = os.path.join(self.output_dir, "figures", "spectra_comparison.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_confusion_matrices(self, results: Dict[str, Dict[str, Dict[str, object]]]) -> str:
        from sklearn.metrics import ConfusionMatrixDisplay, confusion_matrix

        log("  Generating confusion matrices ...")

        fig, axes = plt.subplots(1, 2, figsize=(16, 6))
        fig.suptitle("Classification Results", fontsize=14, fontweight="bold")

        best_multi = max(results["multiclass"], key=lambda k: results["multiclass"][k]["f1_weighted"])
        res_multi = results["multiclass"][best_multi]
        labels_multi = sorted(set(res_multi["y_true"]))
        cm_multi = confusion_matrix(res_multi["y_true"], res_multi["y_pred"], labels=labels_multi)
        ConfusionMatrixDisplay(cm_multi, display_labels=labels_multi).plot(
            ax=axes[0], cmap="Blues", values_format="d", xticks_rotation=45
        )
        axes[0].set_title(f"Multiclass ({best_multi})\nF1={res_multi['f1_weighted']:.3f}")

        best_bin = max(results["binary"], key=lambda k: results["binary"][k]["f1_weighted"])
        res_bin = results["binary"][best_bin]
        labels_bin = ["PASS", "FAIL"]
        cm_bin = confusion_matrix(res_bin["y_true"], res_bin["y_pred"], labels=labels_bin)
        ConfusionMatrixDisplay(cm_bin, display_labels=labels_bin).plot(
            ax=axes[1], cmap="Greens", values_format="d"
        )
        axes[1].set_title(f"Binary ({best_bin})\nF1={res_bin['f1_weighted']:.3f}")

        plt.tight_layout()
        out = os.path.join(self.output_dir, "figures", "confusion_matrices.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close(fig)
        return out

    def plot_feature_importance(self, feature_importance: Dict[str, float]) -> str:
        log("  Generating feature importance plot ...")
        ranked = sorted(feature_importance.items(), key=lambda item: item[1], reverse=True)
        top_n = min(15, len(ranked))
        labels = [name for name, _ in ranked[:top_n]][::-1]
        values = [value for _, value in ranked[:top_n]][::-1]

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.barh(np.arange(top_n), values)
        ax.set_yticks(np.arange(top_n))
        ax.set_yticklabels(labels, fontsize=10)
        ax.set_xlabel("Importance")
        ax.set_title("Top Features for Adulteration Detection", fontweight="bold")
        ax.grid(axis="x", alpha=0.3)

        plt.tight_layout()
        out = os.path.join(self.output_dir, "figures", "feature_importance.png")
        plt.savefig(out, dpi=200, bbox_inches="tight")
        plt.close(fig)
        return out

    def generate_report(self, results: Dict[str, Dict[str, Dict[str, object]]], data_source: str) -> Tuple[str, str]:
        log("  Generating summary report ...")
        lines: List[str] = [
            "=" * 60,
            "EISight Pipeline - Results Summary",
            "=" * 60,
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Data source: {data_source}",
            f"Pipeline version: {cfg.VERSION}",
            f"CV strategy: {results['cv_type']['name']}",
            "",
            f"MULTICLASS RESULTS ({cfg.CV_FOLDS}-fold CV):",
        ]

        for name, res in results["multiclass"].items():
            lines.append(f"  {name:20s} | F1={res['f1_weighted']:.4f} | Acc={res['accuracy']:.4f}")

        lines.append("")
        lines.append(f"BINARY PASS/FAIL RESULTS ({cfg.CV_FOLDS}-fold CV):")
        for name, res in results["binary"].items():
            lines.append(f"  {name:20s} | F1={res['f1_weighted']:.4f} | Acc={res['accuracy']:.4f}")

        if "feature_importance" in results:
            lines.append("")
            lines.append("TOP 5 FEATURES:")
            top5 = sorted(results["feature_importance"].items(), key=lambda item: item[1], reverse=True)[:5]
            for i, (feature, importance) in enumerate(top5, start=1):
                lines.append(f"  {i}. {feature:25s} importance={importance:.4f}")

        report = "\n".join(lines)
        out = os.path.join(self.output_dir, "reports", "summary.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(report)
        return report, out


# ============================================================================
# STAGE 6: INFERENCE BUNDLE
# ============================================================================
class InferenceEngine:
    def __init__(
        self,
        binary_model,
        multiclass_model,
        preprocessor: Preprocessor,
        feature_extractor: FeatureExtractor,
    ) -> None:
        self.binary_model = binary_model
        self.multiclass_model = multiclass_model
        self.preprocessor = preprocessor
        self.feature_extractor = feature_extractor

    def predict_single(
        self,
        Z_mag_array: Sequence[float],
        Z_phase_array: Sequence[float],
        temperature_c: float,
    ) -> Dict[str, object]:
        Z_mag = np.asarray(Z_mag_array, dtype=float).ravel()
        Z_phase = np.asarray(Z_phase_array, dtype=float).ravel()

        if Z_mag.size != Z_phase.size:
            raise ValueError("Z_mag_array and Z_phase_array must have the same length")
        if Z_mag.size != len(self.preprocessor.frequencies):
            raise ValueError(
                f"Expected {len(self.preprocessor.frequencies)} sweep points, got {Z_mag.size}. "
                "Check your real-data frequency grid or preprocessing settings."
            )

        row: Dict[str, float] = {"temperature_C": float(temperature_c), "label": "Unknown", "sample_id": "inference"}
        for i, value in enumerate(Z_mag):
            row[f"Z_mag_{i}"] = float(value)
        for i, value in enumerate(Z_phase):
            row[f"Z_phase_{i}"] = float(value)

        df = pd.DataFrame([row])

        # Apply the SAME preprocessing steps used in training.
        df_clean = self.preprocessor.preprocess(df, verbose=False)
        if len(df_clean) != 1:
            raise ValueError("Single-sweep inference failed QC during preprocessing")
        feats = self.feature_extractor.extract(df_clean, verbose=False)

        multi_probs = self.multiclass_model.predict_proba(feats)[0]
        multi_classes = self.multiclass_model.classes_
        pred_class = str(multi_classes[int(np.argmax(multi_probs))])

        bin_probs = self.binary_model.predict_proba(feats)[0]
        bin_classes = list(self.binary_model.classes_)
        pass_idx = bin_classes.index("PASS") if "PASS" in bin_classes else int(np.argmax(bin_probs))
        pass_prob = float(bin_probs[pass_idx])

        if pass_prob >= cfg.CONFIDENCE_PASS:
            verdict = "PASS"
        elif pass_prob >= cfg.CONFIDENCE_WARN:
            verdict = "WARN"
        else:
            verdict = "FAIL"

        return {
            "verdict": verdict,
            "pure_probability": round(pass_prob, 4),
            "predicted_class": pred_class,
            "multiclass_confidence": round(float(np.max(multi_probs)), 4),
            "multiclass_probabilities": {str(c): round(float(p), 4) for c, p in zip(multi_classes, multi_probs)},
            "binary_probabilities": {str(c): round(float(p), 4) for c, p in zip(bin_classes, bin_probs)},
        }

    def export_bundle(self, output_dir: str) -> Tuple[str, str]:
        bundle = {
            "version": cfg.VERSION,
            "binary_model": self.binary_model,
            "multiclass_model": self.multiclass_model,
            "feature_names": list(self.feature_extractor.feature_names),
            "frequencies_hz": self.feature_extractor.frequencies.tolist(),
            "config": {
                "confidence_pass": cfg.CONFIDENCE_PASS,
                "confidence_warn": cfg.CONFIDENCE_WARN,
                "anchor_freqs_hz": list(cfg.ANCHOR_FREQS_HZ),
            },
            "warning": (
                "This artifact is for local Python inference only. "
                "It is NOT a phone-native Random Forest export."
            ),
        }

        model_path = os.path.join(output_dir, "models", "inference_bundle.joblib")
        joblib.dump(bundle, model_path)

        metadata = {
            "version": cfg.VERSION,
            "model_path": model_path,
            "feature_names": list(self.feature_extractor.feature_names),
            "frequencies_hz": self.feature_extractor.frequencies.tolist(),
            "config": {
                "confidence_pass": cfg.CONFIDENCE_PASS,
                "confidence_warn": cfg.CONFIDENCE_WARN,
                "anchor_freqs_hz": list(cfg.ANCHOR_FREQS_HZ),
            },
            "warning": bundle["warning"],
        }
        metadata_path = os.path.join(output_dir, "models", "model_metadata.json")
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        log(f"  Saved local inference bundle to {model_path}")
        log(f"  Saved model metadata to {metadata_path}")
        return model_path, metadata_path


# ============================================================================
# MAIN PIPELINE
# ============================================================================
def run_pipeline(
    data_filepath: Optional[str] = None,
    force_simulate: bool = False,
    input_format: Optional[str] = None,
    output_dir: str = cfg.OUTPUT_DIR,
) -> Dict[str, object]:
    print("\n" + "=" * 60)
    print(f"  EISight Pipeline v{cfg.VERSION}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60 + "\n")

    ensure_dirs(output_dir)

    raw_spectra: Optional[List[Dict[str, np.ndarray]]] = None

    if data_filepath and not force_simulate:
        fmt = input_format or detect_input_format(data_filepath)
        if fmt == "wide":
            df, frequencies = DataLoader.load_wide(data_filepath)
        elif fmt == "long":
            df, frequencies = DataLoader.load_long(data_filepath)
        else:
            raise ValueError(f"Unsupported input format: {fmt}")
        data_source = "real_ad5933"
    else:
        generator = DataGenerator()
        df, raw_spectra, frequencies = generator.generate_dataset()
        data_source = "simulation"

    raw_path = os.path.join(output_dir, "data", "raw_dataset.csv")
    df.to_csv(raw_path, index=False)
    log(f"  Saved raw dataset to {raw_path}")

    preprocessor = Preprocessor(frequencies=frequencies)
    df_clean = preprocessor.preprocess(df)
    clean_path = os.path.join(output_dir, "data", "preprocessed_dataset.csv")
    df_clean.to_csv(clean_path, index=False)

    extractor = FeatureExtractor(preprocessor.frequencies)
    features_df = extractor.extract(df_clean)
    features_path = os.path.join(output_dir, "data", "features.csv")
    features_df.to_csv(features_path, index=False)

    groups = to_numpy_group_array(df_clean["day"]) if "day" in df_clean.columns else None
    y_multi = to_numpy_str_array(df_clean["label"])
    y_binary = to_numpy_str_array(df_clean["binary_label"])

    trainer = MLTrainer()
    results = trainer.train_and_evaluate(
        X=features_df,
        y_multi=y_multi,
        y_binary=y_binary,
        feature_names=extractor.feature_names,
        groups=groups,
    )

    evaluator = Evaluator(output_dir)
    if raw_spectra is not None:
        evaluator.plot_spectra(raw_spectra, frequencies)
    evaluator.plot_confusion_matrices(results)
    if "feature_importance" in results:
        evaluator.plot_feature_importance(results["feature_importance"])
    report_text, report_path = evaluator.generate_report(results, data_source)

    best_multi_name = trainer.best_multiclass_name
    best_binary_name = trainer.best_binary_name
    assert best_multi_name is not None and best_binary_name is not None

    engine = InferenceEngine(
        binary_model=trainer.final_models[f"binary_{best_binary_name}"],
        multiclass_model=trainer.final_models[f"multi_{best_multi_name}"],
        preprocessor=preprocessor,
        feature_extractor=extractor,
    )
    bundle_path, metadata_path = engine.export_bundle(output_dir)

    if data_source == "simulation":
        log("Stage 7: Demo inference ...")
        demo_generator = DataGenerator(frequencies=frequencies, random_state=cfg.RANDOM_STATE + 123)
        demo_cases = [
            ("Pure milk", None, 0.0),
            ("Milk + 20% water", "water", 20.0),
            ("Milk + 1% urea", "urea", 1.0),
        ]

        print(f"\n{'=' * 60}")
        print("  DEMO: Real-time Inference Simulation")
        print(f"{'=' * 60}")

        for desc, adulterant, conc in demo_cases:
            if adulterant is None:
                params = dict(cfg.PURE_MILK)
            else:
                params = demo_generator.adulterant_params(adulterant, conc)

            Z = demo_generator.cole_cole(demo_generator.omega, **params, noise_std=cfg.NOISE_STD)
            result = engine.predict_single(np.abs(Z), np.degrees(np.angle(Z)), temperature_c=25.0)

            icon = {"PASS": "OK", "WARN": "WARN", "FAIL": "FAIL"}[str(result["verdict"])]
            print(f"\n  Sample: {desc}")
            print(f"  Verdict: {icon} {result['verdict']} (pure_probability={result['pure_probability']:.1%})")
            print(f"  Predicted class: {result['predicted_class']}")
            print(f"  Multiclass confidence: {result['multiclass_confidence']:.1%}")

    run_metadata = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "project_name": cfg.PROJECT_NAME,
        "version": cfg.VERSION,
        "data_source": data_source,
        "output_dir": output_dir,
        "frequencies_hz": preprocessor.frequencies.tolist(),
        "feature_count": len(extractor.feature_names),
        "best_multiclass_model": best_multi_name,
        "best_binary_model": best_binary_name,
        "paths": {
            "raw_dataset": raw_path,
            "preprocessed_dataset": clean_path,
            "features": features_path,
            "report": report_path,
            "model_bundle": bundle_path,
            "model_metadata": metadata_path,
        },
    }
    with open(os.path.join(output_dir, "reports", "run_metadata.json"), "w", encoding="utf-8") as f:
        json.dump(run_metadata, f, indent=2)

    print(f"\n{'=' * 60}")
    print(report_text)
    print(f"\nAll outputs saved to: {output_dir}/")
    print(f"{'=' * 60}\n")

    return {
        "results": results,
        "report_text": report_text,
        "run_metadata": run_metadata,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EISight reviewed ML pipeline")
    parser.add_argument("--data", type=str, help="Path to real AD5933 CSV data")
    parser.add_argument("--simulate", action="store_true", help="Force synthetic simulation mode")
    parser.add_argument(
        "--input-format",
        choices=["wide", "long"],
        default=None,
        help="Optional override for CSV input format",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=cfg.OUTPUT_DIR,
        help="Directory where outputs will be written",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(
        data_filepath=args.data,
        force_simulate=args.simulate,
        input_format=args.input_format,
        output_dir=args.output_dir,
    )
