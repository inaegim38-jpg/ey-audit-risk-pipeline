"""
python/prepare_audit_dataset.py
- BankSim CSV(--csv) 또는 로그정규분포 시뮬레이션으로 거래/증빙 데이터를 생성하고,
  9종 감사 검증 룰(DQ_01~03, EV_01~03, AN_01~03)이 모두 탐지되도록 오류/이상치를 주입해 PostgreSQL에 적재.
"""
import argparse
import hashlib
import uuid
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from sqlalchemy import create_engine

DEFAULT_DB_URI = "postgresql+psycopg2://audit_admin:audit_password123@localhost:5432/audit_db"

def build_dataset(csv_path=None, n_rows: int = 500):
    rng = np.random.default_rng(42)
    categories = ["IT_CONSULTING", "TRAVEL", "ENTERTAINMENT", "OFFICE_SUPPLY", "LOGISTICS"]
    depts = ["RISK_TEAM", "AUDIT_01", "AUDIT_02", "FINANCE", "OPS_TEAM"]
    run_tag = datetime.now().strftime("%H%M%S")
    batch_id = f"BATCH_PY_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    base_dt = datetime(2026, 11, 1, 9, 0, 0)

    if csv_path:
        raw = pd.read_csv(csv_path).head(n_rows)
        n = len(raw)
        tx_df = pd.DataFrame({
            "tx_id": [f"BS_{run_tag}_{i:05d}" for i in range(n)],
            "tx_timestamp": [base_dt + timedelta(hours=int(s)) for s in raw["step"]],
            "account_id": raw["customer"].astype(str).str.replace("'", ""),
            "merchant_id": raw["merchant"].astype(str).str.replace("'", ""),
            "merchant_category": rng.choice(categories, size=n),
            "tx_type": "EXPENSE",
            "currency": "KRW",
            "tx_amount": np.round(raw["amount"].astype(float) * 1400, -2),
            "department_code": rng.choice(depts, size=n),
            "source_dataset": "BANKSIM",
            "is_simulated_fraud": raw["fraud"].astype(bool),
        })
    else:
        days_offset = rng.integers(0, 20, size=n_rows)
        hours_offset = rng.integers(9, 18, size=n_rows)
        mins_offset = rng.integers(0, 60, size=n_rows)
        timestamps = [
            base_dt.replace(hour=0, minute=0) + timedelta(days=int(d), hours=int(h), minutes=int(m))
            for d, h, m in zip(days_offset, hours_offset, mins_offset)
        ]
        tx_df = pd.DataFrame({
            "tx_id": [f"SYN_{run_tag}_{i:05d}" for i in range(n_rows)],
            "tx_timestamp": timestamps,
            "account_id": [f"ACC_{x:03d}" for x in rng.integers(1, 40, size=n_rows)],
            "merchant_id": [f"MER_{x:03d}" for x in rng.integers(1, 25, size=n_rows)],
            "merchant_category": rng.choice(categories, size=n_rows),
            "tx_type": "EXPENSE",
            "currency": "KRW",
            "tx_amount": np.clip(np.round(rng.lognormal(mean=11.9, sigma=0.45, size=n_rows), -2), 10000, 480000),
            "department_code": rng.choice(depts, size=n_rows),
            "source_dataset": "SYNTHETIC_LOGNORMAL",
            "is_simulated_fraud": False,
        })

    tx_df["batch_id"] = batch_id
    tx_df["posting_date"] = pd.to_datetime(tx_df["tx_timestamp"]).dt.date
    tx_df["evidence_doc_no"] = ["EV_" + tid for tid in tx_df["tx_id"]]
    tx_df["approver_id"] = "APR_AUTO"

    ev_df = pd.DataFrame({
        "evidence_id": ["EVID_" + tid for tid in tx_df["tx_id"]],
        "evidence_doc_no": tx_df["evidence_doc_no"],
        "tx_id": tx_df["tx_id"],
        "doc_type": rng.choice(["TAX_INVOICE", "CORP_CARD_SLIP", "RECEIPT"], size=len(tx_df)),
        "issue_date": tx_df["posting_date"],
        "evidence_amount": tx_df["tx_amount"],
        "currency": "KRW",
        "vendor_name": "VENDOR_" + tx_df["merchant_id"],
        "file_hash": [hashlib.sha256(f"{tid}-{uuid.uuid4()}".encode()).hexdigest()[:32] for tid in tx_df["tx_id"]],
        "ocr_confidence": np.round(rng.uniform(93.0, 99.9, size=len(tx_df)), 2),
        "uploaded_by": "pipeline_loader",
    })

    if len(tx_df) >= 60:
        tx_df.loc[0:4, "department_code"] = None                                 # DQ_01
        tx_df.loc[5:9, "tx_amount"] = -35000                                     # DQ_02
        ev_df.loc[10:13, "file_hash"] = "SHARED_DUP_HASH_" + run_tag              # DQ_03
        tx_df.loc[10:13, "is_simulated_fraud"] = True

        tx_df.loc[14:19, "tx_amount"] = 780000                                   # EV_01
        ev_df = ev_df[~ev_df["tx_id"].isin(tx_df.loc[14:19, "tx_id"])].copy()

        tx_df.loc[20:25, "tx_amount"] = 640000                                   # EV_02
        ev_df.loc[ev_df["tx_id"].isin(tx_df.loc[20:25, "tx_id"]), "evidence_amount"] = 410000

        ev_mask_26 = ev_df["tx_id"].isin(tx_df.loc[26:31, "tx_id"])              # EV_03
        ev_df.loc[ev_mask_26, "issue_date"] = pd.to_datetime(ev_df.loc[ev_mask_26, "issue_date"]).dt.date - timedelta(days=45)

        tx_df.loc[32:36, "merchant_category"] = "ENTERTAINMENT"                  # AN_01
        tx_df.loc[32:36, "tx_amount"] = 1450000
        ev_df.loc[ev_df["tx_id"].isin(tx_df.loc[32:36, "tx_id"]), "evidence_amount"] = 1450000

        for pair_idx, base_i in enumerate([38, 40]):                             # AN_02
            ts_split = datetime(2026, 11, 18, 14, 10 + pair_idx * 20)
            tx_df.loc[base_i:base_i+1, "account_id"] = f"ACC_SPLIT_{pair_idx}"
            tx_df.loc[base_i:base_i+1, "merchant_id"] = f"MER_SPLIT_{pair_idx}"
            tx_df.loc[base_i:base_i+1, "tx_timestamp"] = [ts_split, ts_split + timedelta(minutes=4)]
            tx_df.loc[base_i:base_i+1, "posting_date"] = ts_split.date()
            tx_df.loc[base_i:base_i+1, "tx_amount"] = [940000, 910000]
            tx_df.loc[base_i:base_i+1, "is_simulated_fraud"] = True
            ev_df.loc[ev_df["tx_id"] == tx_df.loc[base_i, "tx_id"], ["issue_date", "evidence_amount"]] = [ts_split.date(), 940000]
            ev_df.loc[ev_df["tx_id"] == tx_df.loc[base_i+1, "tx_id"], ["issue_date", "evidence_amount"]] = [ts_split.date(), 910000]

        night_ts = datetime(2026, 11, 19, 2, 15)                                 # AN_03
        tx_df.loc[42:46, "tx_timestamp"] = night_ts
        tx_df.loc[42:46, "posting_date"] = night_ts.date()
        tx_df.loc[42:46, "tx_amount"] = 620000
        ev_df.loc[ev_df["tx_id"].isin(tx_df.loc[42:46, "tx_id"]), ["issue_date", "evidence_amount"]] = [night_ts.date(), 620000]

    return tx_df, ev_df

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-uri", default=DEFAULT_DB_URI)
    parser.add_argument("--csv", default=None, help="Optional BankSim CSV path")
    parser.add_argument("--rows", type=int, default=500)
    args = parser.parse_args()

    tx_df, ev_df = build_dataset(csv_path=args.csv, n_rows=args.rows)
    engine = create_engine(args.db_uri)
    batch_id = tx_df["batch_id"].iloc[0]

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO audit_risk.etl_batch_log (batch_id, source_system, file_name, total_rows, valid_rows, status, completed_at) "
            "VALUES (%s, 'PYTHON_LOADER', %s, %s, %s, 'COMPLETED', NOW())",
            (batch_id, args.csv or f"synthetic_{args.rows}", len(tx_df), len(tx_df)),
        )

    tx_df.to_sql("raw_transactions", engine, schema="audit_risk", if_exists="append", index=False)
    ev_df.to_sql("raw_evidences", engine, schema="audit_risk", if_exists="append", index=False)
    print(f"[OK] Inserted {len(tx_df)} transactions & {len(ev_df)} evidences (batch_id={batch_id})")

if __name__ == "__main__":
    main()
