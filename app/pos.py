from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.models import POSTransaction


IST = ZoneInfo("Asia/Kolkata")


def load_pos_transactions(path: Path) -> list[POSTransaction]:
    df = pd.read_csv(path)
    required = {"invoice_number", "order_date", "order_time", "store_id", "total_amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"POS CSV missing required columns: {sorted(missing)}")

    grouped = (
        df.groupby(["invoice_number", "order_date", "order_time", "store_id"], dropna=False)
        .agg(basket_value_inr=("total_amount", "sum"))
        .reset_index()
    )
    transactions: list[POSTransaction] = []
    for row in grouped.to_dict("records"):
        local_dt = datetime.strptime(
            f"{row['order_date']} {row['order_time']}", "%d-%m-%Y %H:%M:%S"
        ).replace(tzinfo=IST)
        transactions.append(
            POSTransaction(
                store_id=str(row["store_id"]),
                transaction_id=str(row["invoice_number"]),
                timestamp=local_dt.astimezone(UTC),
                basket_value_inr=float(row["basket_value_inr"]),
            )
        )
    return transactions
