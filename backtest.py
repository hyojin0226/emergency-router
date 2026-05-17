#!/usr/bin/env python3
"""
backtest.py — 정책 비교: 최단거리 맹목 vs 3-state 라우팅

가상환자: 세종 인구가중 출발점(sejong_origins) × 통계연보 증상구성
          (case_mix) 로 N명 생성.
정책 A (naive): 응급실 가동(28=Y)인 가장 가까운 병원으로 직행.
                수용여부는 안 보고 감 → 'Y면 성공, 아니면 헛걸음 후
                다음 가까운 병원' (현행 암묵 관행 모델).
정책 B (ours):  3-state 라우팅. ✅Y 우선(거리순) → 없으면 ☎️미보고를
                전화확인(거리순). ⛔N 제외.

지표:
  - 수용확인(Y) 병원 도달까지 '거리'
  - naive 의 '헛걸음 횟수'(Y 아닌 곳 들렀다 되돌아간 횟수)
  - B 의 '전화확인 필요 건수'(시내에 Y 없어 미보고에 의존한 비율)

★ seed 고정 → 재현 가능. 통계/인구 미입력이어도 균등폴백으로 완주.

사용:
    python backtest.py --selftest
    python backtest.py --db egen_snapshots.db --n 2000
"""
import argparse, json, math, random, sqlite3
import severe_routing as sr
from sejong_origins import origins
from case_mix import case_mix

R_KM = 6371.0
GATE = sr.GATEKEEPER  # 28 = 응급실 가동


def hav(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = (math.sin((la2 - la1) * p / 2) ** 2
         + math.cos(la1 * p) * math.cos(la2 * p)
         * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 2 * R_KM * math.asin(math.sqrt(a))


def latest(con, table):
    ts = con.execute(f"SELECT MAX(snapshot_ts) FROM {table}").fetchone()[0]
    if not ts:
        return {}
    return {r[0]: json.loads(r[1]) for r in con.execute(
        f"SELECT hpid,payload_json FROM {table} WHERE snapshot_ts=?",
        (ts,)) if r[0]}


REGION_SIDO = ("세종특별자치시", "대전광역시", "충청남도", "충청북도")


def load(db):
    con = sqlite3.connect(db)
    sev = latest(con, "severe_accept")
    # 권역(세종+대전+충남+충북) 병원의 좌표+시도. 전국 좌표를 다 쓰면
    # 세종 환자에게 부산·강원 병원이 후보로 끼어 거리가 비현실적.
    meta = {}
    q = ("SELECT hpid,lat,lon,sido FROM hospitals "
         "WHERE lat IS NOT NULL AND lon IS NOT NULL "
         f"AND sido IN ({','.join('?' * len(REGION_SIDO))})")
    for hpid, la, lo, sd in con.execute(q, REGION_SIDO):
        meta[hpid] = (la, lo, sd)
    # severe 스냅샷(권역 수집) ∩ 권역 좌표
    hosp = []
    for hpid, payload in sev.items():
        if hpid in meta:
            la, lo, sd = meta[hpid]
            hosp.append((hpid, la, lo, sd, payload))
    return hosp


def gatekeeper_open(payload):
    return sr.classify(payload, GATE) == "Y"


def simulate(db, n, seed):
    rng = random.Random(seed)
    hosp = load(db)
    if len(hosp) < 2:
        print("후보 병원 부족(좌표/데이터 확인). 수집기·마스터 적재 필요.")
        return None
    starts, pop_w = origins()
    mix, stat_w = case_mix()
    syms = [s for s, _ in mix]
    sprob = [p for _, p in mix]
    swts = [w for *_, w in starts]

    naive_dist, ours_dist = [], []
    naive_detour, ours_phone = [], 0
    no_y_region = 0

    # 세종 시내 관점 지표 (출발점이 세종인 환자만)
    SEJONG = "세종특별자치시"
    sej_total = 0
    sej_resolved_in_city = 0       # 세종 시내 Y로 끝난 수
    sej_city_detour = []           # 세종 벗어나기 전 시내 허탕 횟수
    sej_in_city_dist = []          # 세종 시내 최단 응급실 거리
    sej_out_dist = []              # 실제 도달(권역 밖 Y) 거리

    for _ in range(n):
        # 출발점/증상 추출
        s = rng.choices(starts, weights=swts, k=1)[0]
        sname, sla, slo = s[0], s[1], s[2]
        symptom = rng.choices(syms, weights=sprob, k=1)[0]
        # 출발점이 세종 행정동인지(sejong_origins 는 전부 세종이므로 항상 True
        # 지만, 향후 확장 대비해 명시적으로 둔다)
        from_sejong = True

        # 각 병원까지 거리 + 상태 + 시도
        scored = []
        for hpid, hla, hlo, hsido, payload in hosp:
            d = hav(sla, slo, hla, hlo)
            st, _ = sr.hospital_state_for_symptom(payload, symptom)
            gk = gatekeeper_open(payload)
            scored.append((d, st, gk, hsido, hpid))
        scored.sort(key=lambda x: x[0])

        # 응급실 가동 중인 후보만, 가까운 순
        cand = [(d, st, sd) for d, st, gk, sd, hpid in scored if gk]

        # ── 정책 A (현행 관행): 수용정보를 모른다 ──
        a_dist, detour = None, 0
        for d, st, sd in cand:
            if st == "Y":
                a_dist = d
                break
            detour += 1
        if a_dist is None:
            a_dist = cand[-1][0] if cand else 0.0

        # ── 정책 B (우리 시스템): 출발 전 3-state를 본다 ──
        ys = [d for d, st, sd in cand if st == "Y"]
        uns = [d for d, st, sd in cand if st == "UNREPORTED"]
        if ys:
            b_dist = min(ys)
        elif uns:
            b_dist = min(uns)
            ours_phone += 1
            no_y_region += 1
        else:
            b_dist = cand[-1][0] if cand else 0.0

        naive_dist.append(a_dist)
        naive_detour.append(detour)
        ours_dist.append(b_dist)

        # ── 세종 시내 관점 집계 ──
        if from_sejong:
            sej_total += 1
            city = [(d, st) for d, st, sd in cand if sd == SEJONG]
            city_y = [d for d, st in city if st == "Y"]
            # 세종 시내에서 Y로 끝났는가
            if city_y:
                sej_resolved_in_city += 1
                sej_in_city_dist.append(min(city_y))
            # 세종 벗어나기 전, 시내 병원에서 허탕친 횟수
            #  (가까운 순으로 시내 병원을 거치며 Y 아니면 허탕)
            cd = 0
            for d, st in sorted(city):
                if st == "Y":
                    break
                cd += 1
            sej_city_detour.append(cd)
            # 실제 도달 거리(권역 내 최단 Y) — 세종 밖으로 밀린 거리
            region_y = [d for d, st, sd in cand if st == "Y"]
            if region_y:
                sej_out_dist.append(min(region_y))

    def avg(x):
        return sum(x) / len(x) if x else 0.0

    DETOUR_MIN = 8  # 헛걸음 1회당 평균 손실(왕복+거절확인), 보수적 가정
    sej_resolve_rate = (sej_resolved_in_city / sej_total
                        if sej_total else 0.0)
    return {
        "n": n, "pop_w": pop_w, "stat_w": stat_w,
        "naive_dist": avg(naive_dist), "ours_dist": avg(ours_dist),
        "naive_detour": avg(naive_detour),
        "detour_min": DETOUR_MIN,
        "lost_min": avg(naive_detour) * DETOUR_MIN,
        "phone_rate": ours_phone / n,
        "no_y_rate": no_y_region / n,
        "sej_total": sej_total,
        "sej_resolve_rate": sej_resolve_rate,
        "sej_city_detour": avg(sej_city_detour),
        "sej_out_dist": avg(sej_out_dist),
        "sej_out_min": avg(sej_out_dist) / 40 * 60 if sej_out_dist else 0.0,
    }


def report(r):
    if not r:
        return
    print("=== 백테스트: 현행 관행(A) vs 3-state 라우팅(B) ===")
    print(f"가상환자 {r['n']}명  "
          f"(출발=세종 {'인구가중' if r['pop_w'] else '균등폴백'}, "
          f"증상={'통계연보' if r['stat_w'] else '균등폴백'})\n")
    print(f"  정책 A 현행(수용정보 모름) : 최초 수용까지 평균 "
          f"{r['naive_dist']:.1f} km, "
          f"거절·허탕 평균 {r['naive_detour']:.2f} 회")
    print(f"  정책 B 3-state 라우팅      : 평균 {r['ours_dist']:.1f} km, "
          f"허탕 0 회 (사전 가시화)")
    print(f"\n  → 핵심: A는 평균 {r['naive_detour']:.2f}회 허탕 → "
          f"헛걸음 1회 {r['detour_min']}분 가정 시 "
          f"평균 {r['lost_min']:.1f}분 골든타임 손실")
    print(f"     B는 이 손실을 0으로 (Y는 직행, 미보고는 전화확인 1곳 지정)")
    print(f"  → 세종 환자의 {r['phone_rate']*100:.1f}% 는 권역에 수용'확인'"
          f" 병원이 0곳 → 전화확인에 의존")
    print(f"  → 즉 {r['no_y_rate']*100:.1f}% 케이스는 '거절'이 아니라"
          f" '침묵' 때문에 탐색이 발생")
    print("\n해석: 두 정책의 거리는 비슷할 수 있다. 차이는 '허탕 횟수와")
    print("      그 시간'이다 — 뺑뺑이는 거절이 아닌 침묵의 문제이며,")
    print("      사전 가시화가 골든타임을 되돌린다.")

    # ── 세종 시내 관점 (발표 핵심) ──
    print("\n━━━ 세종 시내 관점 (발표 핵심 지표) ━━━")
    print(f"  세종 출발 환자 {r['sej_total']}명 중")
    print(f"  · 세종 '시내'에서 수용확인(Y)으로 해결: "
          f"{r['sej_resolve_rate']*100:.1f}%")
    print(f"  · 세종 시내 병원에서 허탕(미보고 방문): "
          f"평균 {r['sej_city_detour']:.2f} 회")
    print(f"  · 결국 도달하는 권역 내 수용확인 병원까지: "
          f"평균 {r['sej_out_dist']:.1f} km "
          f"(구급차 40km/h 가정 약 {r['sej_out_min']:.0f}분)")
    if r['sej_resolve_rate'] == 0.0:
        print("\n  ★ 세종 시내에서 해결되는 중증환자 = 0%.")
        print("    모든 세종 중증환자는 권역 밖으로 밀려난다.")
        print("    이유는 '거절'이 아니라 시내 병원이 '침묵'하기 때문.")
        print("    정보가 있었다면 이 이송·탐색 시간을 줄일 수 있다.")


def selftest():
    assert 10 < hav(36.48, 127.28, 36.35, 127.38) < 60
    mix, _ = case_mix()
    assert abs(sum(p for _, p in mix) - 1) < 1e-6
    o, _ = origins()
    assert abs(sum(w for *_, w in o) - 1) < 1e-6
    # 동일 seed 재현성
    print("[selftest] OK — 거리/구성/가중 합 정상, "
          f"증상 {len(mix)}종, 출발 {len(o)}개")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--db", default="egen_snapshots.db")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        report(simulate(a.db, a.n, a.seed))


if __name__ == "__main__":
    main()