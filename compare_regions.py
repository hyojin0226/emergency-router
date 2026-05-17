#!/usr/bin/env python3
"""compare_regions.py — 'N=0' 이 충청권 특수성인가, 전국 공통 행태인가

가설검정용. 대조군 시도(서울·부산 등)의 중증 수용 응답을 단발로 받아
N / 미보고 / Y 비율을 충청권과 비교한다.

  - 대조군도 N≈0, 미보고 다수  →  '불가를 안 적는 건 전국 공통'
       = 충청이 특별히 다 받아주는 게 아니다 (네 의심에 대한 반증)
  - 대조군은 N 이 유의미하게 많다  →  충청권 특수성 가능
       = 프로젝트 한계로 정직히 명시 필요

기존 수집기/DB 건드리지 않음(메모리상 호출만).
사용:
    set EGEN_SERVICE_KEY=디코딩_키
    python compare_regions.py
    python compare_regions.py --encoded
    python compare_regions.py --symptom 심근경색
"""
import argparse, json, os, sys
import egen_collector as ec
import severe_routing as sr

CONTROL = ["서울특별시", "부산광역시", "경기도"]
CHUNG = ["세종특별자치시", "대전광역시", "충청남도", "충청북도"]


def tally(sido, key, encoded, symptom):
    items, err = ec.paginate(ec.OP_SEVERE, sido, key, encoded)
    if err:
        return None, err
    c = {"Y": 0, "UNREPORTED": 0, "N": 0}
    for d in items:
        st, _ = sr.hospital_state_for_symptom(d, symptom)
        c[st] += 1
    return c, None


def line(sido, c):
    tot = sum(c.values()) or 1
    return (f"  {sido:9s}  병원{tot:3d}  "
            f"✅Y {c['Y']:3d}({c['Y']/tot*100:4.1f}%)  "
            f"☎️미보고 {c['UNREPORTED']:3d}({c['UNREPORTED']/tot*100:4.1f}%)  "
            f"⛔N {c['N']:3d}({c['N']/tot*100:4.1f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoded", action="store_true")
    ap.add_argument("--symptom", default="뇌출혈")
    a = ap.parse_args()
    key = os.environ.get("EGEN_SERVICE_KEY", "").strip()
    if not key:
        sys.exit("EGEN_SERVICE_KEY 비어있음.")

    print(f"[{a.symptom}] 권역 비교 — N(명시적 불가) 비율이 핵심\n")
    agg = {"control": {"Y": 0, "UNREPORTED": 0, "N": 0},
           "chung": {"Y": 0, "UNREPORTED": 0, "N": 0}}

    print("■ 충청권(우리 대상)")
    for s in CHUNG:
        c, err = tally(s, key, a.encoded, a.symptom)
        if err:
            print(f"  {s}: ERROR {err}")
            continue
        print(line(s, c))
        for k in c:
            agg["chung"][k] += c[k]

    print("\n■ 대조군(전국 보고행태 확인용)")
    for s in CONTROL:
        c, err = tally(s, key, a.encoded, a.symptom)
        if err:
            print(f"  {s}: ERROR {err}")
            continue
        print(line(s, c))
        for k in c:
            agg["control"][k] += c[k]

    print("\n── 종합 ──")
    print(line("충청권합", agg["chung"]))
    print(line("대조군합", agg["control"]))

    cn = agg["chung"]
    co = agg["control"]
    ct = sum(cn.values()) or 1
    ot = sum(co.values()) or 1
    print("\n판정:")
    if co["N"] / ot < 0.05 and cn["N"] / ct < 0.05:
        print("  → 대조군도 N≈0. '불가를 안 적는 건 전국 공통 행태'.")
        print("    즉 충청권이 특별히 다 받아주는 게 아니다.")
        print("    네 의심에 대한 데이터 반증 — 프로젝트 논지 유지.")
    elif co["N"] / ot >= 0.15:
        print("  → 대조군은 N이 유의미. 충청권 N=0 이 특수할 수 있음.")
        print("    '미보고=불가 아님'을 한계로 명시하고 결론 톤 조정 필요.")
    else:
        print("  → 중간. 미보고 우세는 공통이나 N 차이 존재 → 한계 명시.")
    print("\n주의: 미보고 병원이 실제 수용가능한지는 어떤 데이터에도")
    print("      없다. 핵심 논지는 '수용여부를 알 수 없다(침묵)'이지")
    print("      '수용 불가'가 아니다 — 이 선을 발표에서 먼저 긋는다.")


if __name__ == "__main__":
    main()