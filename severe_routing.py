#!/usr/bin/env python3
"""
severe_routing.py — 중증질환 수용가능 매핑 / 3-state 분류 / 권역 공백 분석

설계 근거: 공공데이터포털 공식 명세
  - 값은 Y(가능)/N(불가) 두 가지만 정의됨.
  - 따라서 우리가 본 '정보미제공'은 '병원이 해당 필드를 응답에서 누락'한 것.
    => 3-state: 'Y'=가능 / 'N'=명시적 불가 / 'UNREPORTED'=미보고(전화확인)
  - mkioskty28 = 질환 아님. '응급실 가동(gate keeper)'. 전제조건으로만.

사용:
    python severe_routing.py --selftest
    python severe_routing.py --gap egen_snapshots.db    # 권역 미제공률
    python severe_routing.py --classify egen_snapshots.db --symptom 뇌출혈
"""
import argparse, json, sqlite3, sys
from collections import defaultdict

# ── 공식 명세 그대로 (라우팅 점수 대상은 1..27, 28은 제외) ──
MKIOSK = {
    1:  "[재관류중재술] 심근경색",
    2:  "[재관류중재술] 뇌경색",
    3:  "[뇌출혈수술] 거미막하출혈",
    4:  "[뇌출혈수술] 거미막하출혈 외",
    5:  "[대동맥응급] 흉부",
    6:  "[대동맥응급] 복부",
    7:  "[담낭담관질환] 담낭질환",
    8:  "[담낭담관질환] 담도포함질환",
    9:  "[복부응급수술] 비외상",
    10: "[장중첩/폐색] 영유아",
    11: "[응급내시경] 성인 위장관",
    12: "[응급내시경] 영유아 위장관",
    13: "[응급내시경] 성인 기관지",
    14: "[응급내시경] 영유아 기관지",
    15: "[저체중출생아] 집중치료",
    16: "[산부인과응급] 분만",
    17: "[산부인과응급] 산과수술",
    18: "[산부인과응급] 부인과수술",
    19: "[중증화상] 전문치료",
    20: "[사지접합] 수족지접합",
    21: "[사지접합] 수족지접합 외",
    22: "[응급투석] HD",
    23: "[응급투석] CRRT",
    24: "[정신과적응급] 폐쇄병동입원",
    25: "[안과적수술] 응급",
    26: "[영상의학혈관중재] 성인",
    27: "[영상의학혈관중재] 영유아",
}
GATEKEEPER = 28  # 응급실 가동 여부. 전제조건, 점수 제외.

# ── 증상 → 필요한 mkioskty 코드. 한 증상이 여러 코드면 ANY(하나라도 Y면 가능) ──
# 보수적 원칙: 환자를 잘못 보내는 것보다 후보를 넓게 두고 3-state로 구분.
SYMPTOM_TO_CODES = {
    "심근경색":       [1],
    "뇌경색":         [2],
    "뇌출혈":         [3, 4],          # 거미막하 + 그 외
    "대동맥응급":     [5, 6],
    "복부응급":       [7, 8, 9],
    "영유아장폐색":   [10],
    "성인소화관출혈": [11],            # 응급내시경 성인 위장관
    "기도이물":       [13],            # 성인 기관지 내시경
    "고위험분만":     [16, 17],
    "부인과응급":     [18],
    "중증화상":       [19],
    "절단지접합":     [20, 21],
    "급성신부전":     [22, 23],
    "정신과응급":     [24],
    "안과응급":       [25],
    "혈관중재성인":   [26],
}


def classify(payload: dict, code: int) -> str:
    """단일 코드의 3-state. 키는 'MKioskTy{n}' (대소문 혼용 방어)."""
    for key in (f"MKioskTy{code}", f"mkioskty{code}", f"MKIOSKTY{code}"):
        if key in payload:
            v = (payload[key] or "").strip().upper()
            if v == "Y":
                return "Y"
            if v.startswith("N"):
                return "N"
            if v == "" or "미제공" in (payload[key] or ""):
                return "UNREPORTED"
            return "UNREPORTED"
    return "UNREPORTED"  # 필드 자체가 응답에 없음 = 미보고


def hospital_state_for_symptom(payload: dict, symptom: str):
    """증상에 대한 병원 종합 상태.
    반환: (state, detail)
      state: 'Y'(하나라도 가능) / 'N'(전부 명시 불가) / 'UNREPORTED'(불가확인 안됨)
    우선순위 규칙:
      - 필요한 코드 중 하나라도 Y  -> 'Y'
      - Y 없고 하나라도 UNREPORTED -> 'UNREPORTED' (전화확인 후보)
      - 전부 N                     -> 'N' (제외)
    또한 gatekeeper(28)가 N이면 응급실 미가동 -> 강제 'N'.
    """
    codes = SYMPTOM_TO_CODES.get(symptom)
    if codes is None:
        raise KeyError(f"미정의 증상: {symptom}. 가능: {list(SYMPTOM_TO_CODES)}")
    gk = classify(payload, GATEKEEPER)
    per = {c: classify(payload, c) for c in codes}
    if gk == "N":
        return "N", {"gatekeeper": gk, **per}
    states = set(per.values())
    if "Y" in states:
        return "Y", {"gatekeeper": gk, **per}
    if "UNREPORTED" in states:
        return "UNREPORTED", {"gatekeeper": gk, **per}
    return "N", {"gatekeeper": gk, **per}


# ── 권역 미제공률 분석 (최신 스냅샷 기준) ──
def latest_snapshot_rows(con):
    ts = con.execute(
        "SELECT MAX(snapshot_ts) FROM severe_accept").fetchone()[0]
    if not ts:
        return ts, []
    rows = con.execute(
        "SELECT sido,duty_name,payload_json FROM severe_accept "
        "WHERE snapshot_ts=?", (ts,)).fetchall()
    return ts, rows


def gap_report(db_path):
    con = sqlite3.connect(db_path)
    ts, rows = latest_snapshot_rows(con)
    if not rows:
        print("severe_accept 비어있음. 수집기 먼저 돌려라.")
        return
    print(f"[최신 스냅샷] {ts}  병원 {len(rows)}곳\n")
    print("병원별 27개 중증항목 미보고 수:")
    per_sido = defaultdict(lambda: [0, 0])  # sido -> [미보고합, 항목합]
    worst = []
    for sido, name, pj in rows:
        p = json.loads(pj)
        un = sum(1 for c in range(1, 28) if classify(p, c) == "UNREPORTED")
        per_sido[sido][0] += un
        per_sido[sido][1] += 27
        worst.append((un, sido, name))
    for un, sido, name in sorted(worst, reverse=True):
        bar = "█" * round(un / 27 * 20)
        print(f"  {un:2d}/27 {bar:<20} {name} ({sido})")
    print("\n시도별 평균 미보고율:")
    for sido, (u, t) in sorted(per_sido.items()):
        print(f"  {sido}: {u}/{t} = {u/t*100:4.1f}%")
    print("\n해석: 미보고율이 높을수록 구급대원이 전화로 확인할 수밖에 "
          "없는 영역 = 뺑뺑이 발생 메커니즘.")


def classify_symptom(db_path, symptom):
    con = sqlite3.connect(db_path)
    ts, rows = latest_snapshot_rows(con)
    if not rows:
        print("데이터 없음.")
        return
    buckets = {"Y": [], "UNREPORTED": [], "N": []}
    for sido, name, pj in rows:
        st, _ = hospital_state_for_symptom(json.loads(pj), symptom)
        buckets[st].append(f"{name}({sido})")
    print(f"[{symptom}] 최신 {ts} 기준 권역 분류 "
          f"(코드 {SYMPTOM_TO_CODES[symptom]} = "
          f"{[MKIOSK[c] for c in SYMPTOM_TO_CODES[symptom]]})\n")
    print(f"  ✅ 수용가능(Y) {len(buckets['Y'])}곳: "
          f"{', '.join(buckets['Y']) or '-'}")
    print(f"  ☎️ 미보고→전화확인 {len(buckets['UNREPORTED'])}곳: "
          f"{', '.join(buckets['UNREPORTED']) or '-'}")
    print(f"  ⛔ 명시적 불가(N) {len(buckets['N'])}곳: "
          f"{', '.join(buckets['N']) or '-'}")
    print("\n라우팅: ✅ 우선(거리·병상여력순) → 없으면 ☎️ "
          "(전화 우선순위) → ⛔ 제외")


# ── selftest ──
def selftest():
    # Y 우선
    p = {"MKioskTy3": "Y", "MKioskTy4": "N", "MKioskTy28": "Y"}
    assert hospital_state_for_symptom(p, "뇌출혈")[0] == "Y"
    # 전부 N -> N
    p = {"MKioskTy3": "N", "MKioskTy4": "N", "MKioskTy28": "Y"}
    assert hospital_state_for_symptom(p, "뇌출혈")[0] == "N"
    # 하나 미보고, Y 없음 -> UNREPORTED
    p = {"MKioskTy3": "N", "MKioskTy28": "Y"}  # ty4 키 없음
    assert hospital_state_for_symptom(p, "뇌출혈")[0] == "UNREPORTED"
    # gatekeeper N -> 강제 N (응급실 미가동)
    p = {"MKioskTy3": "Y", "MKioskTy4": "Y", "MKioskTy28": "N"}
    assert hospital_state_for_symptom(p, "뇌출혈")[0] == "N"
    # '정보미제공' 문자열도 UNREPORTED 로
    assert classify({"MKioskTy1": "정보미제공"}, 1) == "UNREPORTED"
    # 대소문자 혼용 키 방어
    assert classify({"mkioskty1": "Y"}, 1) == "Y"
    print("[selftest] OK — 3-state/게이트키퍼/대소문자/미보고 규칙 통과")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--gap", metavar="DB")
    ap.add_argument("--classify", metavar="DB")
    ap.add_argument("--symptom", default="뇌출혈")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.gap:
        gap_report(a.gap)
    elif a.classify:
        classify_symptom(a.classify, a.symptom)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()