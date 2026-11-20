#!/usr/bin/env python3
"""합성 포렌식 케이스 코퍼스 생성기.

실제 기업·개인 데이터를 쓰지 않고, 문서 검토(document review)와 증거 관리
파이프라인을 검증하기 위한 가상 케이스를 만든다.

케이스: 한빛물산(주) 구매팀 법인카드·협력사 지급 관련 제보
주입 패턴은 ground_truth.json 에 기록하되, 탐지 단계에서는 읽지 않는다.
(정답 누설을 막기 위해 파이프라인은 ground_truth 를 참조하지 않는다.)
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20261121
CASE_ID = "CASE-2026-014"
COMPANY = "한빛물산(주)"

PEOPLE = {
    "김도현": {"title": "구매팀장", "email": "dhkim@hanbitmc.co.kr", "dept": "구매팀"},
    "박서연": {"title": "구매팀 대리", "email": "sypark@hanbitmc.co.kr", "dept": "구매팀"},
    "이준호": {"title": "재무팀 과장", "email": "jhlee@hanbitmc.co.kr", "dept": "재무팀"},
    "최민지": {"title": "총무팀 사원", "email": "mjchoi@hanbitmc.co.kr", "dept": "총무팀"},
    "정미숙": {"title": "세영산업 대표", "email": "msjung@seyoung.co.kr", "dept": "외부"},
}

VENDORS = {
    "대성기업": {"biz_no": "214-81-33510", "contact": "02-3472-1180", "addr": "서울 금천구 디지털로 128"},
    "한빛테크": {"biz_no": "105-86-41207", "contact": "031-789-2244", "addr": "경기 성남시 분당구 판교로 256"},
    "세영산업": {"biz_no": "134-87-20993", "contact": "02-3472-1180", "addr": "서울 금천구 디지털로 128"},
    "우성물산": {"biz_no": "220-81-77142", "contact": "02-556-9031", "addr": "서울 강남구 테헤란로 44"},
    "태광ENG": {"biz_no": "312-85-19004", "contact": "041-552-7788", "addr": "충남 천안시 서북구 3공단로 19"},
}
NORMAL_VENDORS = ["대성기업", "한빛테크", "우성물산", "태광ENG"]
RELATED_VENDOR = "세영산업"

ITEMS = [
    ("사무용 노트북 15인치", 1_750_000),
    ("서버 랙 냉각 유닛", 4_200_000),
    ("사무실 파티션 시공", 3_100_000),
    ("CCTV 교체 공사", 2_450_000),
    ("정기 청소 용역(3개월)", 5_400_000),
    ("복합기 임대료(연간)", 2_880_000),
    ("창고 선반 설치", 1_920_000),
    ("보안 소프트웨어 라이선스", 6_600_000),
    ("차량 정비 및 소모품", 1_180_000),
    ("회의실 AV 장비", 3_750_000),
]

NORMAL_SUBJECTS = [
    "주간 구매 진행 현황 공유", "신규 협력사 등록 서류 확인 요청", "3분기 단가 협의 결과",
    "납기 일정 조정 건", "세금계산서 수취 지연 안내", "구매팀 월간 실적 정리",
    "재고 실사 일정 공지", "협력사 평가표 회신 요청", "예산 집행률 점검",
    "계약서 원본 보관 위치", "입고 검수 결과 공유", "구매 요청 양식 변경 안내",
    "물류센터 방문 일정", "연간 단가계약 갱신 검토", "구매 시스템 권한 신청",
    "반기 협력사 실사 결과", "소모품 재고 알림", "견적 비교표 양식 문의",
    "운송비 정산 내역", "품질 이슈 접수 현황", "신규 품목 샘플 요청",
]

SENTENCE_POOL = [
    "지난주 논의한 건은 담당자 확인 후 다음 주 초에 정리하겠습니다.",
    "해당 협력사의 사업자등록증 갱신본을 아직 받지 못했습니다.",
    "단가 인상 요청은 3.2% 수준으로 조정해서 재검토 중입니다.",
    "납기 지연 사유는 자재 수급 문제라고 회신을 받았습니다.",
    "재고 실사 결과는 첨부한 집계표와 큰 차이가 없습니다.",
    "예산 집행률이 전월 대비 4.1%p 올랐습니다.",
    "계약서 원본은 총무팀 문서고 A-3에 보관되어 있습니다.",
    "품의서 결재선은 팀장님과 재무팀 과장님 두 분으로 되어 있습니다.",
    "입고 검수는 다음 주 화요일 오전으로 잡았습니다.",
    "거래명세서와 세금계산서 금액이 일치하는지 다시 확인 부탁드립니다.",
    "협력사 평가표는 품질 40점, 납기 30점, 가격 30점 기준입니다.",
    "이번 달 발주 건수는 총 37건으로 집계되었습니다.",
    "운송비 정산은 월말 마감 후 일괄 처리하고 있습니다.",
    "샘플 테스트 결과는 다음 주 수요일까지 회신 가능하다고 합니다.",
    "시스템 권한 신청은 정보보안팀 승인 후 반영됩니다.",
    "견적 비교표 양식은 작년 개정본을 그대로 쓰면 됩니다.",
    "품질 이슈 2건은 협력사에서 재작업 후 납품 예정입니다.",
    "연간 단가계약 갱신 대상은 12개사입니다.",
    "물류센터 방문은 인원 조정 후 다시 공지하겠습니다.",
    "소모품 재고가 안전재고 이하로 떨어진 품목이 3개 있습니다.",
    "계약 갱신 시 특약사항 검토가 필요하다는 의견이 있었습니다.",
    "발주 취소 건은 이번 주 내로 시스템에 반영하겠습니다.",
    "월간 보고자료는 매월 5영업일까지 제출하면 됩니다.",
    "협력사 담당자 연락처가 변경되어 공유드립니다.",
    "검수 기준은 입고 수량과 외관 상태 위주로 확인합니다.",
    "예산 이월 가능 여부는 재무팀에 문의해두었습니다.",
    "해당 품목은 대체 공급사가 두 곳 있어 비교견적이 가능합니다.",
    "반품 처리 건은 3일 내 회수 예정입니다.",
    "계약서 날인본은 스캔 후 공유드리겠습니다.",
    "정기 점검 일정은 매월 첫째 주 목요일입니다.",
]

NORMAL_OPENERS = [
    "안녕하세요, {dept} {name}입니다.",
    "수고 많으십니다. {dept} {name}입니다.",
    "{dept} {name}입니다. 아래와 같이 공유드립니다.",
    "바쁘신 중에 죄송합니다. {dept} {name}입니다.",
]

NORMAL_CLOSERS = [
    "확인 부탁드립니다.\n\n감사합니다.",
    "회신 주시면 반영하겠습니다.\n\n감사합니다.",
    "일정상 어려우시면 알려주세요.\n\n감사합니다.",
    "검토 후 의견 부탁드립니다.",
    "궁금한 점 있으시면 연락 주세요.",
]

SUSPICIOUS_EMAILS = [
    {
        "key": "PRESSURE_QUOTE",
        "subject": "RE: 세영산업 견적 관련",
        "body": "세영산업 견적서 금액은 저희 쪽에서 정리해서 보내드릴게요. 다른 업체 견적은 형식만 맞춰주시고,\n"
                "금액은 세영과 동일하게 올려주세요. 비교표는 제가 마무리하겠습니다.\n\n"
                "이 건은 제가 직접 챙기니 다른 분께는 공유하지 말아주세요.",
        "from": "김도현", "to": "박서연",
    },
    {
        "key": "SPLIT_ORDER",
        "subject": "한도 관련해서",
        "body": "이번 건 금액이 커서 한 번에 올리면 결재선이 길어집니다.\n"
                "10시(1,000만원) 아래로 나눠서 4번 올려주세요. 날짜는 이번 주 안에서 적당히 배분하고,\n"
                "품명은 조금씩 다르게 적어주세요.",
        "from": "김도현", "to": "박서연",
    },
    {
        "key": "RELATED_PARTY",
        "subject": "주말에 정리한 서류",
        "body": "여보, 세영산업 관련 서류 내가 집에서 정리해뒀어. 회사 메일로는 보내지 말고\n"
                "이 주소로만 연락해줘. 회사에서는 같은 주소 쓰는 거 티나니까 조심하고.",
        "from": "김도현", "to": "정미숙(개인)",
    },
    {
        "key": "DUPLICATE_PAYMENT",
        "subject": "세영산업 3월분 지급 재요청",
        "body": "3월분 세금계산서가 아직 미지급으로 떠서 다시 요청드립니다.\n"
                "이미 지급된 건지 확인이 안 되니 일단 재지급 처리 부탁드립니다. 번호는 동일합니다.",
        "from": "김도현", "to": "이준호",
    },
    {
        "key": "CARD_PRIVATE",
        "subject": "법인카드 사용 내역 문의",
        "body": "지난주 금요일 카드 내역 중 골프장 건은 접대비로 처리해주세요.\n"
                "참석자는 정리해서 따로 드리겠습니다. 영수증은 없습니다.",
        "from": "김도현", "to": "최민지",
    },
]


def _d(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def _ts(rng: random.Random, d: date, start_h: int = 8, end_h: int = 19) -> datetime:
    return datetime(d.year, d.month, d.day, rng.randint(start_h, end_h), rng.choice([0, 10, 20, 30, 40, 50]))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _eml(msg_id: str, frm: str, to: str, dt: datetime, subject: str, body: str,
         cc: str = "", in_reply_to: str = "") -> str:
    lines = [
        f"Message-ID: <{msg_id}@hanbitmc.co.kr>",
        f"Date: {dt.strftime('%a, %d %b %Y %H:%M:%S +0900')}",
        f"From: {frm}",
        f"To: {to}",
    ]
    if cc:
        lines.append(f"Cc: {cc}")
    lines.append(f"Subject: {subject}")
    if in_reply_to:
        lines.append(f"In-Reply-To: <{in_reply_to}@hanbitmc.co.kr>")
    lines.append("MIME-Version: 1.0")
    lines.append('Content-Type: text/plain; charset="utf-8"')
    lines.append("")
    lines.append(body)
    return "\n".join(lines) + "\n"


def build(out_dir: Path, seed: int = SEED) -> dict:
    rng = random.Random(seed)
    out_dir = Path(out_dir)
    raw = out_dir / "raw"
    gt = {"case_id": CASE_ID, "company": COMPANY, "patterns": [], "files": {}}

    start, end = date(2025, 9, 1), date(2026, 6, 30)

    # ------------------------------------------------------------------ emails
    email_dir = raw / "emails"
    msg_seq = 0
    email_index = []

    def new_msg() -> str:
        nonlocal msg_seq
        msg_seq += 1
        return f"HB{msg_seq:05d}"

    # 정상 메일
    for n in range(300):
        sender = rng.choice(list(PEOPLE))
        if sender in ("정미숙",):
            sender = "박서연"
        recipient = rng.choice([p for p in PEOPLE if p != sender and p != "정미숙"])
        d = _d(rng, start, end)
        dt = _ts(rng, d)
        subj = rng.choice(NORMAL_SUBJECTS)
        picked = rng.sample(SENTENCE_POOL, k=rng.randint(3, 6))
        body = "\n".join([
            rng.choice(NORMAL_OPENERS).format(dept=PEOPLE[sender]["dept"], name=PEOPLE[sender]["title"]),
            "",
            f"문서번호: REF-{d.strftime('%Y%m')}-{n:04d}",
            f"기준일: {d.isoformat()}",
            "",
            *[f"- {s}" for s in picked],
            "",
            rng.choice(NORMAL_CLOSERS),
        ])
        mid = new_msg()
        fname = f"{dt.strftime('%Y%m%d_%H%M')}_{mid}.eml"
        _write(email_dir / fname, _eml(mid, PEOPLE[sender]["email"], PEOPLE[recipient]["email"],
                                       dt, subj, body))
        email_index.append({"msg_id": mid, "file": f"emails/{fname}", "date": dt.isoformat(),
                            "from": sender, "to": recipient, "subject": subj})

    # 의심 메일 (스레드 형태로 2~3통씩)
    for spec in SUSPICIOUS_EMAILS:
        base_d = _d(rng, date(2026, 2, 1), date(2026, 6, 20))
        root_id = new_msg()
        dt0 = _ts(rng, base_d, 9, 11)
        frm = PEOPLE.get(spec["from"], {"email": "dhkim@hanbitmc.co.kr"})["email"]
        to_key = spec["to"].replace("(개인)", "")
        to = PEOPLE.get(to_key, {"email": "external@example.com"})["email"]
        if spec["to"].endswith("(개인)"):
            to = "msjung.personal@naver.com"
        fname0 = f"{dt0.strftime('%Y%m%d_%H%M')}_{root_id}.eml"
        _write(email_dir / fname0,
               _eml(root_id, frm, to, dt0, spec["subject"], spec["body"]))
        email_index.append({"msg_id": root_id, "file": f"emails/{fname0}", "date": dt0.isoformat(),
                            "from": spec["from"], "to": spec["to"], "subject": spec["subject"]})
        gt["patterns"].append({"pattern": spec["key"], "evidence": f"emails/{fname0}",
                               "note": spec["subject"]})

        # 답장 1~2통
        for k in range(rng.randint(1, 2)):
            dtk = dt0 + timedelta(hours=rng.randint(2, 30))
            rid = new_msg()
            reply_body = rng.choice([
                "네, 알겠습니다. 말씀하신 대로 처리하겠습니다.",
                "확인했습니다. 다만 증빙은 어떻게 첨부할까요?",
                "일정은 제가 맞춰서 다시 올리겠습니다.",
            ])
            fnamek = f"{dtk.strftime('%Y%m%d_%H%M')}_{rid}.eml"
            _write(email_dir / fnamek,
                   _eml(rid, to, frm, dtk, "RE: " + spec["subject"], reply_body, in_reply_to=root_id))
            email_index.append({"msg_id": rid, "file": f"emails/{fnamek}", "date": dtk.isoformat(),
                                "from": spec["to"], "to": spec["from"], "subject": "RE: " + spec["subject"]})

    # --------------------------------------------------------------- approvals
    approval_dir = raw / "approvals"
    approvals = []
    for i in range(1, 61):
        d = _d(rng, start, end)
        vendor = rng.choice(NORMAL_VENDORS)
        item, unit = rng.choice(ITEMS)
        qty = rng.randint(1, 2)
        amount = unit * qty
        doc_no = f"AP-2026-{i:04d}"
        text = (
            f"품 의 서\n\n"
            f"문서번호: {doc_no}\n"
            f"기안일자: {d.isoformat()}\n"
            f"기안부서: 구매팀\n"
            f"기안자: 박서연\n"
            f"협력사: (주){vendor}\n"
            f"품명: {item}\n"
            f"수량: {qty}\n"
            f"금액: {amount:,}원 (부가세 포함)\n"
            f"예산과목: 일반관리비-소모품비\n"
            f"납기요청일: {(d + timedelta(days=rng.randint(7, 30))).isoformat()}\n"
            f"결재: 팀장 김도현 / 재무팀 이준호\n\n"
            f"사유: {item} 교체 및 보강 건으로 상기와 같이 품의합니다.\n"
        )
        _write(approval_dir / f"{doc_no}.txt", text)
        approvals.append({"doc_no": doc_no, "date": d.isoformat(), "vendor": vendor,
                          "amount": amount, "file": f"approvals/{doc_no}.txt"})

    # 분할발주 4건 (한도 1,000만원 직하)
    split_base = date(2026, 4, 6)
    split_amounts = [9_450_000, 9_380_000, 9_620_000, 9_510_000]
    split_items = ["사무실 파티션 시공(1차)", "사무실 파티션 시공(2차)",
                   "사무실 파티션 시공(3차)", "사무실 파티션 시공(4차)"]
    for i, (amt, item) in enumerate(zip(split_amounts, split_items), start=61):
        d = split_base + timedelta(days=3 * (i - 61))
        doc_no = f"AP-2026-{i:04d}"
        text = (
            f"품 의 서\n\n문서번호: {doc_no}\n기안일자: {d.isoformat()}\n기안부서: 구매팀\n"
            f"기안자: 박서연\n협력사: (주){RELATED_VENDOR}\n품명: {item}\n수량: 1\n"
            f"금액: {amt:,}원 (부가세 포함)\n예산과목: 일반관리비-시설유지비\n"
            f"납기요청일: {(d + timedelta(days=14)).isoformat()}\n결재: 팀장 김도현\n\n"
            f"사유: 사무실 레이아웃 변경에 따른 파티션 시공(구간 {i - 60}/4).\n"
        )
        _write(approval_dir / f"{doc_no}.txt", text)
        approvals.append({"doc_no": doc_no, "date": d.isoformat(), "vendor": RELATED_VENDOR,
                          "amount": amt, "file": f"approvals/{doc_no}.txt"})
        gt["patterns"].append({"pattern": "SPLIT_ORDER", "evidence": f"approvals/{doc_no}.txt",
                               "amount": amt})

    # ---------------------------------------------------------------- quotes
    quote_dir = raw / "quotes"
    quotes = []
    for i in range(1, 81):
        d = _d(rng, start, end)
        vendor = rng.choice(NORMAL_VENDORS)
        item, unit = rng.choice(ITEMS)
        qty = rng.randint(1, 3)
        total = unit * qty
        contact = VENDORS[vendor]["contact"]
        text = (
            f"견 적 서\n\n"
            f"견적번호: Q-{i:04d}\n"
            f"견적일자: {d.isoformat()}\n"
            f"공급자: (주){vendor}\n"
            f"사업자번호: {VENDORS[vendor]['biz_no']}\n"
            f"주소: {VENDORS[vendor]['addr']}\n"
            f"담당자 연락처: {contact}\n\n"
            f"품명: {item}\n수량: {qty}\n단가: {unit:,}원\n합계금액: {total:,}원 (VAT 포함)\n"
            f"견적유효기간: {(d + timedelta(days=30)).isoformat()}\n\n"
            f"상기와 같이 견적을 제출합니다.\n"
        )
        _write(quote_dir / f"Q-{i:04d}.txt", text)
        quotes.append({"quote_no": f"Q-{i:04d}", "date": d.isoformat(), "vendor": vendor,
                       "amount": total, "file": f"quotes/Q-{i:04d}.txt"})

    # 허위 견적 3건: 금액 동일 + 연락처 동일
    for j, vendor in enumerate(["세영산업", "대성기업", "우성물산"], start=81):
        d = date(2026, 4, 2) + timedelta(days=j - 81)
        amount = 9_500_000
        text = (
            f"견 적 서\n\n견적번호: Q-{j:04d}\n견적일자: {d.isoformat()}\n공급자: (주){vendor}\n"
            f"사업자번호: {VENDORS[vendor]['biz_no']}\n주소: {VENDORS[vendor]['addr']}\n"
            f"담당자 연락처: 02-3472-1180\n\n"
            f"품명: 사무실 파티션 시공\n수량: 1\n단가: {amount:,}원\n합계금액: {amount:,}원 (VAT 포함)\n"
            f"견적유효기간: {(d + timedelta(days=30)).isoformat()}\n\n상기와 같이 견적을 제출합니다.\n"
        )
        _write(quote_dir / f"Q-{j:04d}.txt", text)
        quotes.append({"quote_no": f"Q-{j:04d}", "date": d.isoformat(), "vendor": vendor,
                       "amount": amount, "file": f"quotes/Q-{j:04d}.txt"})
        gt["patterns"].append({"pattern": "FAKE_QUOTE", "evidence": f"quotes/Q-{j:04d}.txt",
                               "amount": amount})

    # --------------------------------------------------------------- invoices
    invoice_dir = raw / "invoices"
    invoices = []
    for i in range(1, 81):
        d = _d(rng, start, end)
        vendor = rng.choice(NORMAL_VENDORS)
        item, unit = rng.choice(ITEMS)
        supply = unit * rng.randint(1, 2)
        vat = int(supply * 0.1)
        total = supply + vat
        inv_no = f"2026-{i:06d}"
        contract_d = d - timedelta(days=rng.randint(3, 25))
        text = (
            f"전자세금계산서\n\n"
            f"승인번호: {inv_no}\n"
            f"작성일자: {d.isoformat()}\n"
            f"공급자: (주){vendor} / {VENDORS[vendor]['biz_no']}\n"
            f"공급받는자: {COMPANY} / 220-81-00000\n"
            f"품목: {item}\n공급가액: {supply:,}원\n세액: {vat:,}원\n합계: {total:,}원\n"
            f"계약일자: {contract_d.isoformat()}\n"
        )
        _write(invoice_dir / f"INV-{i:04d}.txt", text)
        invoices.append({"invoice_no": inv_no, "issue_date": d.isoformat(), "vendor": vendor,
                         "supply": supply, "total": total, "contract_date": contract_d.isoformat(),
                         "file": f"invoices/INV-{i:04d}.txt"})

    # 중복 지급 대상 1건 (동일 승인번호 2회 지급)
    dup_amount = 8_740_000
    for k, d in enumerate([date(2026, 3, 12), date(2026, 4, 9)], start=81):
        text = (
            f"전자세금계산서\n\n승인번호: 2026-000777\n작성일자: {d.isoformat()}\n"
            f"공급자: (주){RELATED_VENDOR} / {VENDORS[RELATED_VENDOR]['biz_no']}\n"
            f"공급받는자: {COMPANY} / 220-81-00000\n품목: 사무실 파티션 시공\n"
            f"공급가액: {int(dup_amount / 1.1):,}원\n세액: {dup_amount - int(dup_amount / 1.1):,}원\n"
            f"합계: {dup_amount:,}원\n계약일자: 2026-03-02\n"
        )
        _write(invoice_dir / f"INV-{k:04d}.txt", text)
        invoices.append({"invoice_no": "2026-000777", "issue_date": d.isoformat(),
                         "vendor": RELATED_VENDOR, "supply": int(dup_amount / 1.1),
                         "total": dup_amount, "contract_date": "2026-03-02",
                         "file": f"invoices/INV-{k:04d}.txt"})
    gt["patterns"].append({"pattern": "DUPLICATE_PAYMENT", "evidence": "invoices/INV-0081.txt, invoices/INV-0082.txt",
                           "amount": dup_amount})

    # 날짜 역전 2건 (계약일 > 발행일)
    for k, (inv_i, issue, contract) in enumerate([(83, "2026-05-04", "2026-05-19"),
                                                  (84, "2026-05-11", "2026-05-26")], start=0):
        text = (
            f"전자세금계산서\n\n승인번호: 2026-0009{k}{k}\n작성일자: {issue}\n"
            f"공급자: (주){RELATED_VENDOR} / {VENDORS[RELATED_VENDOR]['biz_no']}\n"
            f"공급받는자: {COMPANY} / 220-81-00000\n품목: 사무실 파티션 시공\n"
            f"공급가액: 4,300,000원\n세액: 430,000원\n합계: 4,730,000원\n"
            f"계약일자: {contract}\n"
        )
        _write(invoice_dir / f"INV-{inv_i:04d}.txt", text)
        gt["patterns"].append({"pattern": "DATE_MANIPULATION", "evidence": f"invoices/INV-{inv_i:04d}.txt",
                               "note": f"작성일자 {issue} < 계약일자 {contract}"})

    # ------------------------------------------------------------- card usage
    card_dir = raw / "card"
    card_rows = ["거래일시,가맹점,업종,금액,카드소지자,승인번호,비고"]
    for i in range(200):
        d = _d(rng, start, end)
        dt = _ts(rng, d, 8, 20)
        merchant, cat = rng.choice([
            ("스타벅스 여의도점", "음식점"), ("이마트 금천점", "유통"), ("GS칼텍스", "주유"),
            ("쿠팡", "온라인"), ("한빛문구", "사무용품"), ("교보문고", "도서"),
            ("김밥천국", "음식점"), ("KT", "통신"), ("서울주차장", "주차"),
        ])
        amount = rng.choice([4_500, 8_000, 12_000, 34_000, 56_000, 120_000, 250_000])
        card_rows.append(f"{dt.strftime('%Y-%m-%d %H:%M')},{merchant},{cat},{amount},김도현,"
                         f"{rng.randint(10000000, 99999999)},{''}")

    # 사적 사용 의심 6건
    for dt, merchant, cat, amount in [
        (datetime(2026, 3, 7, 21, 40), "그린필드CC", "골프장", 480_000),
        (datetime(2026, 4, 11, 22, 15), "루나라운지", "유흥주점", 1_250_000),
        (datetime(2026, 4, 25, 23, 5), "루나라운지", "유흥주점", 980_000),
        (datetime(2026, 5, 9, 20, 30), "그린필드CC", "골프장", 520_000),
        (datetime(2026, 5, 23, 21, 10), "제이스카이", "유흥주점", 1_430_000),
        (datetime(2026, 6, 6, 19, 50), "그린필드CC", "골프장", 460_000),
    ]:
        card_rows.append(f"{dt.strftime('%Y-%m-%d %H:%M')},{merchant},{cat},{amount},김도현,"
                         f"{rng.randint(10000000, 99999999)},접대비 처리 요청")
        gt["patterns"].append({"pattern": "CARD_PRIVATE", "evidence": "card/card_transactions.csv",
                               "note": f"{merchant} {amount:,}원"})
    _write(card_dir / "card_transactions.csv", "\n".join(card_rows) + "\n")

    # -------------------------------------------------------------- minutes
    minute_dir = raw / "minutes"
    for i in range(1, 21):
        d = _d(rng, start, end)
        text = (
            f"회의록\n\n일시: {d.isoformat()} 10:00~11:00\n장소: 본사 3층 회의실\n"
            f"참석: 김도현, 박서연, 이준호, 최민지\n\n"
            f"안건 1. 구매팀 월간 진행 현황\n- 일반관리비 집행률 {(rng.randint(60, 95))}%\n"
            f"- 협력사 평가 일정 조율\n\n"
            f"안건 2. 기타\n- 차주 결재 건 사전 점검\n"
        )
        _write(minute_dir / f"MIN-{i:03d}.txt", text)

    gt["files"] = {
        "emails": len(list(email_dir.glob('*.eml'))),
        "approvals": len(list(approval_dir.glob('*.txt'))),
        "quotes": len(list(quote_dir.glob('*.txt'))),
        "invoices": len(list(invoice_dir.glob('*.txt'))),
        "minutes": len(list(minute_dir.glob('*.txt'))),
        "card": 1,
    }
    (out_dir / "ground_truth.json").write_text(
        json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
    return gt


def main() -> None:
    ap = argparse.ArgumentParser(description="합성 포렌식 케이스 코퍼스 생성")
    ap.add_argument("--out", default="data/case_2026_014", help="출력 디렉터리")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    gt = build(Path(args.out), args.seed)
    total = sum(gt["files"].values())
    print(f"[build_corpus] {gt['case_id']} 생성 완료: 문서 {total}건 → {args.out}/raw")
    for k, v in gt["files"].items():
        print(f"  - {k}: {v}")


if __name__ == "__main__":
    main()
