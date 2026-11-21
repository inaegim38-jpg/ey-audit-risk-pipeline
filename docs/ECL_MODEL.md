# Phase 2 — FX-AR / IFRS 9 ECL 시뮬레이터

## 목적

`sql/16_ecl_simulator.sql`은 FX 표시 매출채권(AR_FX)과 원화 매출채권(AR)을 같은 분석 grain으로 집계한 뒤, 환율 충격 시나리오별 ECL을 재산출한다. 결과는 기존 `fact_risk_finding`을 덮어쓰지 않고 `audit_core.fact_ecl_recalc`에 저장한다. 따라서 기존 FX-02 기준선과 별도의 모델 결과를 비교할 수 있다.

## 입력과 기준

| 항목 | Phase 2 입력 |
|---|---|
| 대상 | `ENG-2026-001`, 최근 전표기간 `202603` |
| 대상 계정 | `std_kifrs_code IN ('AR', 'AR_FX')` |
| 분석 grain | 시나리오 × counterparty × currency |
| FX 입력 | `fact_journal.krw_amount`, `currency_code` |
| PD/LGD | `dim_pd_lgd_curve`: KSIC `29199`, `UNRATED`, 1년 PD 4.0%, LGD 45.0% |
| 할인율 | 3.5%, 1년 할인계수 `POWER(1 + 3.5%, -1)` |
| 충당금 비교값 | `PROVISION` 공시 footnote의 `ecl_allowance` 320,000,000원 |
| 단위 | KRW, 원 단위 저장·요약 반올림 |

위 PD/LGD와 할인율은 합성 데이터에서 재현 가능한 **시뮬레이션 가정**이다. 실제 고객 업무에서는 승인된 내부등급·PD/LGD curve·stage·회수율·거시 시나리오와 결산일 환율을 입력으로 교체해야 한다.

## 계산식

비원화 통화의 stressed EAD는 다음과 같이 계산한다.

```text
stressed EAD = base EAD × (1 + FX shock)
```

원화 exposure에는 FX shock을 적용하지 않는다. ECL은 다음과 같다.

```text
ECL = stressed EAD × PD × LGD × discount factor
 discount factor = (1 + discount rate)^(-horizon years)
```

Booked allowance는 총 대상 EAD에 대한 원장별 base EAD 비례 배부값이다. `gap_amount = ECL - booked allowance`이며, `gap_pct`는 stressed ECL 대비 절대 gap 비율이다.

## 시나리오 설계

초기 원안의 3개 band(BASE / ±10% / ±20%)를 보존하면서 방향별 민감도를 확인할 수 있도록 5개 point grid를 구현했다.

- `BASE`: 0%
- `FX_UP_10`, `FX_DN_10`: ±10%
- `FX_UP_20`, `FX_DN_20`: ±20%

즉, 문서상 “3 bands”는 BASE, STRESS_10, STRESS_20을 뜻하고, 데이터에는 상승·하락 방향을 각각 point로 표현한다.

## 실행

```bash
export PGPASSWORD='...'
./scripts/bootstrap.sh
# 또는 Phase 0/1 DB가 이미 있으면
psql -f sql/16_ecl_simulator.sql
```

기본 실행은 `run_ecl_scenarios('ENG-2026-001', FALSE)`로 분석 테이블만 갱신한다. `TRUE`를 전달할 때만 scenario별 FX-02 보조 finding을 `ECL:<scenario>:<period>` ref_id로 방출한다.

## 실행 결과 (2026-11-21, 합성 DB)

| scenario | shock | exposure | FX exposure | base EAD (원) | stressed EAD (원) | ECL (원) | gap (원) | gap % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FX_DN_20 | -20% | 66 | 33 | 12,204,065,000 | 10,992,735,800 | 191,178,014 | -128,821,986 | 67.38% |
| FX_DN_10 | -10% | 66 | 33 | 12,204,065,000 | 11,598,400,400 | 201,711,311 | -118,288,689 | 58.64% |
| BASE | 0% | 66 | 33 | 12,204,065,000 | 12,204,065,000 | 212,244,609 | -107,755,391 | 50.77% |
| FX_UP_10 | +10% | 66 | 33 | 12,204,065,000 | 12,809,729,600 | 222,777,906 | -97,222,094 | 43.64% |
| FX_UP_20 | +20% | 66 | 33 | 12,204,065,000 | 13,415,394,200 | 233,311,204 | -86,688,797 | 37.16% |

상승 충격에서 USD EAD와 ECL이 증가하고, 하락 충격에서 감소한다. 원화 exposure는 각 scenario에서 동일하다.

## Top gap

`vw_ecl_top_gaps`는 `ABS(gap_amount)` 내림차순으로 원장 grain의 우선 검토 대상을 제공한다. 이 뷰의 top 5를 실행 시점에 조회하며, 특정 고객의 승인된 PD/LGD를 의미하는 결과가 아니라 합성 입력을 사용한 추가검토 후보이다.

실행 시점 top 5는 다음과 같다. 동일한 KRW exposure가 FX shock 방향과 무관하게 반복되는 것은 원화 exposure에 환율 충격을 적용하지 않기 때문이다.

| scenario | exposure_ref | currency | base EAD (원) | ECL (원) | gap (원) | gap % |
|---|---|---|---:|---:|---:|---:|
| FX_DN_20 | UNMAPPED:USD | USD | 1,104,501,000 | 15,366,970 | -13,593,898 | 88.4618% |
| FX_DN_20 | `...0008:KRW` | KRW | 1,441,408,000 | 25,067,965 | -12,726,865 | 50.7694% |
| FX_DN_10 | `...0008:KRW` | KRW | 1,441,408,000 | 25,067,965 | -12,726,865 | 50.7694% |
| FX_UP_10 | `...0008:KRW` | KRW | 1,441,408,000 | 25,067,965 | -12,726,865 | 50.7694% |
| FX_UP_20 | `...0008:KRW` | KRW | 1,441,408,000 | 25,067,965 | -12,726,865 | 50.7694% |

## 기존 FX-02와의 차이

기존 룰은 `dim_industry_stats.metric = 'ECL_COVERAGE_RATIO'`의 1.8% 근사를 사용해 finding을 만든다. Phase 2는 원장 AR/AR_FX를 counterparty·통화별로 집계하고, FX shock과 PD/LGD·할인계수를 명시적으로 적용한다. 두 결과는 목적과 입력이 다르므로 자동으로 대체하지 않는다. 기본 실행 후 기존 `fact_risk_finding`은 40건으로 유지된다.

## IFRS 9 적용 한계

이 구현은 IFRS 9 ECL 개념을 설명하는 **단기 horizon 시뮬레이터**이지 고객 재무보고용 산출 모델이 아니다. 현재 미구현 항목은 lifetime ECL, Stage 1/2/3 분류, SICR 판단, forward-looking macroeconomic weighting, default definition, recovery cash-flow timing, multiple approved PD/LGD curves, collateral·보증·회수비용, validation/back-testing이다. 실제 감사·재무보고 판단에는 고객 정책과 승인된 모델, 원천 증거, 전문가 검토가 필요하다.
