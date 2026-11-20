"""
python/load_banksim_real.py
실제 BankSim 거래 데이터(Kaggle ealaxi/banksim1, bs140513_032310.csv)를 감사 검증 스키마에 적재.

[실제 값 그대로 사용] customer, merchant, category, amount, fraud(사기 라벨), step(상대 일자)
[BankSim에 없어서 가정으로 채움 — README/리포트에 명시]
  - tx_timestamp : step(일) -> --base-date 기준 날짜, 시각은 12:00 고정 (원본에 시각 정보 없음)
  - amount       : 유로 -> 원화 환산(--fx, 기본 1400)
  - department_code : 고객 연령대 코드(AGE_n)
  - 증빙(raw_evidences): 공개 데이터에 증빙이 없어 거래에서 파생한 합성 증빙.
                         --no-evidence-defects 를 주지 않으면 소량(약 2~10%)의 증빙 결함을 시뮬레이션으로 주입.
  - baseline_stats: 가정치가 아니라 적재된 실제 데이터에서 카테고리별 평균/표준편차를 계산
"""
import argparse
import hashlib
import io
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DEFAULT_DB_URI = "postgresql+psycopg2://audit_admin:audit_password123@localhost:5432/audit_db"

TX_COLS = ["tx_id", "batch_id", "tx_timestamp", "posting_date", "account_id", "merchant_id",
           "merchant_category", "tx_type", "currency", "tx_amount", "department_code",
           "evidence_doc_no", "approver_id", "source_dataset", "is_simulated_fraud"]
EV_COLS = ["evidence_id", "evidence_doc_no", "tx_id", "doc_type", "issue_date", "evidence_amount",
           "currency", "vendor_name", "file_hash", "ocr_confidence", "uploaded_by"]


def build(csv_path, rows, fx, base_date, seed, defects):
    rng = np.random.default_rng(seed)
    raw = pd.read_csv(csv_path, quotechar="'")
    total = len(raw)
    if rows and rows < total:
        raw = raw.sample(n=rows, random_state=seed).sort_values("step").reset_index(drop=True)
    n = len(raw)
    batch_id = f"BATCH_BANKSIM_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    ts = [datetime.combine(base_date, datetime.min.time()) + timedelta(days=int(s), hours=12) for s in raw["step"]]

    tx = pd.DataFrame({
        "tx_id": [f"BS_{int(s):03d}_{i:06d}" for i, s in enumerate(raw["step"])],
        "batch_id": batch_id,
        "tx_timestamp": ts,
        "account_id": raw["customer"].astype(str).str.strip("'"),
        "merchant_id": raw["merchant"].astype(str).str.strip("'"),
        "merchant_category": raw["category"].astype(str).str.strip("'"),
        "tx_type": "EXPENSE",
        "currency": "KRW",
        "tx_amount": np.round(raw["amount"].astype(float) * fx, -2),
        "department_code": "AGE_" + raw["age"].astype(str).str.strip("'"),
        "approver_id": "APR_AUTO",
        "source_dataset": "BANKSIM_REAL",
        "is_simulated_fraud": raw["fraud"].astype(int).astype(bool),
    })
    tx["posting_date"] = pd.to_datetime(tx["tx_timestamp"]).dt.date
    tx["evidence_doc_no"] = "EV_" + tx["tx_id"]

    ev = pd.DataFrame({
        "evidence_id": "EVID_" + tx["tx_id"],
        "evidence_doc_no": tx["evidence_doc_no"],
        "tx_id": tx["tx_id"],
        "doc_type": rng.choice(["TAX_INVOICE", "CORP_CARD_SLIP", "RECEIPT"], size=n),
        "issue_date": tx["posting_date"],
        "evidence_amount": tx["tx_amount"],
        "currency": "KRW",
        "vendor_name": "VENDOR_" + tx["merchant_id"],
        "file_hash": [hashlib.sha256(t.encode()).hexdigest()[:32] for t in tx["tx_id"]],
        "ocr_confidence": np.round(rng.uniform(93.0, 99.9, size=n), 2),
        "uploaded_by": "pipeline_loader",
    })

    stats = {}
    if defects:
        high = (tx["tx_amount"] >= 500000).to_numpy()
        drop = high & (rng.random(n) < 0.10)                     # EV_01: 고액 거래 일부의 증빙 누락
        mism = (~drop) & (rng.random(n) < 0.02)                  # EV_02: 금액 불일치
        ev.loc[mism, "evidence_amount"] = np.round(ev.loc[mism, "evidence_amount"] * 0.7, -2)
        late = (~drop) & (rng.random(n) < 0.02)                  # EV_03: 증빙 발행일 45일 괴리
        ev.loc[late, "issue_date"] = [d - timedelta(days=45) for d in ev.loc[late, "issue_date"]]
        dup_idx = np.flatnonzero((~drop) & (rng.random(n) < 0.005))   # DQ_03: 증빙 파일 해시 중복
        if len(dup_idx) >= 2:
            ev.loc[dup_idx, "file_hash"] = "SHARED_DUP_HASH_" + batch_id[-6:]
        ev = ev[~drop].copy()
        stats = {"drop": int(drop.sum()), "mismatch": int(mism.sum()), "late": int(late.sum()), "dup": int(len(dup_idx))}
    return tx, ev, batch_id, total, stats


def copy_df(conn, df, table, cols):
    buf = io.StringIO()
    df[cols].to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    cur = conn.cursor()
    cur.copy_expert(f"COPY audit_risk.{table} ({','.join(cols)}) FROM STDIN WITH (FORMAT csv, NULL '\\N')", buf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="bs140513_032310.csv 경로")
    ap.add_argument("--rows", type=int, default=20000, help="무작위 표본 수 (0 = 전체 59만 행)")
    ap.add_argument("--fx", type=float, default=1400.0, help="EUR->KRW 환산율(가정)")
    ap.add_argument("--base-date", default="2026-01-01", help="step 0에 해당하는 날짜(가정)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-evidence-defects", action="store_true", help="증빙 결함 시뮬레이션 주입 끄기")
    ap.add_argument("--keep-existing", action="store_true", help="기존 거래/증빙/탐지 결과를 지우지 않음")
    ap.add_argument("--db-uri", default=DEFAULT_DB_URI)
    a = ap.parse_args()

    tx, ev, batch_id, total, st = build(a.csv, a.rows, a.fx, datetime.strptime(a.base_date, "%Y-%m-%d").date(),
                                        a.seed, not a.no_evidence_defects)
    engine = create_engine(a.db_uri)
    with engine.begin() as c:
        if not a.keep_existing:
            c.execute(text("TRUNCATE audit_risk.dq_anomaly_findings, audit_risk.raw_evidences, audit_risk.raw_transactions RESTART IDENTITY"))
            c.execute(text("DELETE FROM audit_risk.baseline_stats"))
        c.execute(text("INSERT INTO audit_risk.etl_batch_log (batch_id, source_system, file_name, total_rows, valid_rows, status, completed_at) "
                       "VALUES (:b, 'BANKSIM_REAL', :f, :t, :v, 'COMPLETED', NOW())"),
                  {"b": batch_id, "f": a.csv.replace("\\", "/").split("/")[-1], "t": len(tx), "v": len(tx)})
        raw = c.connection
        copy_df(raw, tx, "raw_transactions", TX_COLS)
        copy_df(raw, ev, "raw_evidences", EV_COLS)
        # 기준선: 실제 데이터(0원 초과)에서 카테고리별 평균/표준편차 계산
        c.execute(text("""
            INSERT INTO audit_risk.baseline_stats (merchant_category, avg_amount, stddev_amount)
            SELECT merchant_category, ROUND(AVG(tx_amount),2), ROUND(COALESCE(STDDEV_SAMP(tx_amount),0),2)
            FROM audit_risk.raw_transactions WHERE tx_amount > 0 GROUP BY merchant_category
            ON CONFLICT (merchant_category) DO UPDATE SET avg_amount=EXCLUDED.avg_amount, stddev_amount=EXCLUDED.stddev_amount"""))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_tx_acc ON audit_risk.raw_transactions (account_id, merchant_id, posting_date)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_ev_tx ON audit_risk.raw_evidences (tx_id)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_ev_doc ON audit_risk.raw_evidences (evidence_doc_no)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS ix_f_tx ON audit_risk.dq_anomaly_findings (tx_id)"))
    print(f"[OK] BankSim 원본 {total:,}행 중 {len(tx):,}행 적재, 증빙 {len(ev):,}건 (batch_id={batch_id})")
    print(f"     실제 사기 라벨 건수: {int(tx['is_simulated_fraud'].sum()):,}")
    if st:
        print(f"     [시뮬레이션] 증빙 결함 주입: {st}")


if __name__ == "__main__":
    main()
