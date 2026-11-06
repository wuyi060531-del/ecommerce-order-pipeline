"""Production transforms extracted from the project's existing conversion contracts.

See docs/PROVENANCE.md for human and AI contribution boundaries.
"""
from __future__ import annotations

import pandas as pd


def remove_exact_duplicates(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop_duplicates()


def fill_missing_city(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    result["city"] = result["city"].fillna("未知")
    return result


def keep_positive_quantity(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[frame["quantity"] > 0].copy(deep=True)


def keep_known_price(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[frame["unit_price"].notna()].copy(deep=True)


def convert_order_time(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    result["order_time"] = pd.to_datetime(
        result["order_time"], format="%Y-%m-%d %H:%M:%S", errors="coerce",
    )
    return result


def add_order_amount(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy(deep=True)
    result["order_amount"] = result["quantity"] * result["unit_price"]
    return result


def keep_paid_orders(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[frame["status"] == "paid"].copy(deep=True)


def summarize_city_amount(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.groupby("city", as_index=False)["order_amount"].sum().copy(deep=True)
