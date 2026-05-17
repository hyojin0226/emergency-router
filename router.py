#!/usr/bin/env python3
"""
router.py — 세종 권역 중증 라우팅 엔진

조립:
  severe_routing.py  : 증상 → 3-state(Y/UNREPORTED/N)
  egen_snapshots.db  : severe_accept(최신) + er_beds(최신) + hospitals(좌표)
  sejong_origins.py  : 세종 인구가중 출발점

순위 규칙 (뺑뺑이 메커니즘을 정직하게 반영):
  TIER 1  ✅ Y(수용확인)  — 거리 + 병상여력 점수로 정렬
  TIER 2  ☎️ UNREPORTED  — '명시적 불가가 아님'. 전화확인 후보.
                           거리 + 병상여력순. (세종은 여기 많음)
  TIER 3  ⛔ N            — 제외
출력: 각 병원의 거리/여력/사유. "왜 세종 밖으로 나가야 하나"가 드러남.

사용:
    python router.py --selftest
    python router.py --db egen_snapshots.db --symptom 뇌출혈
    python router.py --db egen_snapshots.db --symptom 심근경색 --origin 보람동
"""
import argparse, json, math, sqlite3, sys
import severe_routing as sr
from sejong_origins import origins, SEJONG_DONG

R_KM = 6371.0


def haversine(la1, lo1, la2, lo2):
    p = math.pi / 180
    a = (math.sin((la2 - la1) * p / 2) ** 2
         + math.cos(la1 * p) * math.cos(la2 * p)
         * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 2 * R_KM * math.asin(math.sqrt(a))


def _to_int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def latest(con, table):
    ts = con.execute(f"SELECT MAX(snapshot_ts) FROM {table}").fetchone()[0]
    if not ts:
        return ts, {}
    rows = con.execute(
        f"SELECT hpid,duty_name,payload_json FROM {table} "
        f"WHERE snapshot_ts=?", (ts,)).fetchall()
    return ts, {r[0]: (r[1], json.loads(r[2])) for r in rows if r[0]}


REGION_SIDO = ("세종특별자치시", "대전광역시", "충청남도", "충청북도")


def hospital_coords(con):
    out = {}
    q = ("SELECT hpid,duty_name,lat,lon FROM hospitals "
         "WHERE lat IS NOT NULL AND lon IS NOT NULL "
         f"AND sido IN ({','.join('?' * len(REGION_SIDO))})")
    for hpid, name, lat, lon in con.execute(q, REGION_SIDO):
        out[hpid] = (name, lat, lon)
    return out


def bed_score(bed_payload):
    """병상여력 점수. 클수록 여유. 핵심: hvec(응급실 가용).
    음수/없음은 0 이하로 처리(자리 없음)."""
    if not bed_payload:
        return None
    hvec = _to_int(bed_payload.get("hvec"))
    hvoc = _to_int(bed_payload.get("hvoc"))   # 수술실
    hvicc = _to_int(bed_payload.get("hvicc"))  # 중환자실
    parts = [v for v in (hvec, hvoc, hvicc) if v is not None]
    if not parts:
        return None
    # 가중: 응급실 우선
    s = 0
    if hvec is not None:
        s += max(hvec, 0) * 2
    if hvoc is not None:
        s += max(hvoc, 0)
    if hvicc is not None:
        s += max(hvicc, 0)
    return s


def route(db_path, symptom, origin_name=None, topn=12):
    con = sqlite3.connect(db_path)
    sev_ts, sev = latest(con, "severe_accept")
    bed_ts, bed = latest(con, "er_beds")
    coords = hospital_coords(con)
    if not sev:
        print("severe_accept 비어있음. 수집기 먼저.")
        return

    # 출발점 결정
    if origin_name:
        match = [d for d in SEJONG_DONG if d[0] == origin_name]
        if not match:
            print(f"미정의 출발점: {origin_name}")
            print("가능:", ", ".join(d[0] for d in SEJONG_DONG))
            return
        n, la, lo, _ = match[0]
        start_pts = [(n, la, lo, 1.0)]
        weighted = None
    else:
        start_pts, weighted = origins()

    # 대표 출발점(인구가중 최대 동, 또는 지정 동) — 단일 순위표 출력용
    rep = max(start_pts, key=lambda x: x[3])
    rep_name, rla, rlo = rep[0], rep[1], rep[2]

    rows = []
    for hpid, (name, payload) in sev.items():
        state, detail = sr.hospital_state_for_symptom(payload, symptom)
        if state == "N":
            continue  # TIER 3 제외
        c = coords.get(hpid)
        if not c:
            dist = None
        else:
            dist = haversine(rla, rlo, c[1], c[2])
        bp = bed.get(hpid, (None, None))[1]
        bs = bed_score(bp)
        tier = 1 if state == "Y" else 2
        rows.append({
            "hpid": hpid, "name": name, "state": state, "tier": tier,
            "dist": dist, "bed": bs,
            "hvec": _to_int(bp.get("hvec")) if bp else None,
        })

    # 정렬: tier ↑ → 거리 ↑(없으면 큰 값) → 병상여력 ↓
    def keyf(r):
        return (r["tier"],
                r["dist"] if r["dist"] is not None else 9e9,
                -(r["bed"] if r["bed"] is not None else -1))
    rows.sort(key=keyf)

    print(f"=== 라우팅 [{symptom}] ===")
    print(f"severe 스냅샷 {sev_ts} / beds {bed_ts}")
    if weighted is False:
        print("출발점: 세종 균등가중(인구 미입력) — 대표동", rep_name)
    elif weighted is True:
        print(f"출발점: 세종 인구가중 / 대표동 {rep_name}")
    else:
        print(f"출발점: 지정 {rep_name}")
    print(f"증상코드 {sr.SYMPTOM_TO_CODES[symptom]} = "
          f"{[sr.MKIOSK[c] for c in sr.SYMPTOM_TO_CODES[symptom]]}\n")

    icon = {1: "✅", 2: "☎️"}
    label = {1: "수용확인", 2: "미보고→전화확인"}
    shown_t2 = False
    for i, r in enumerate(rows[:topn], 1):
        if r["tier"] == 2 and not shown_t2:
            print("  ── 여기부터 수용 미확인(전화확인 권장) ──")
            shown_t2 = True
        d = f"{r['dist']:5.1f}km" if r["dist"] is not None else "거리?"
        ec = f"응급실{r['hvec']}" if r["hvec"] is not None else "여력?"
        print(f"  {i:2d}. {icon[r['tier']]} {r['name']}  {d}  {ec}  "
              f"[{label[r['tier']]}]")

    t1 = [r for r in rows if r["tier"] == 1]
    nearest_y = min((r["dist"] for r in t1 if r["dist"] is not None),
                    default=None)
    print()
    if nearest_y is not None:
        print(f"※ 수용 '확인'된 가장 가까운 병원까지 {nearest_y:.1f} km "
              f"(세종 {rep_name} 기준).")
        print("  세종 시내에 Y 확인 병원이 없으면 이 거리가 곧 "
              "'세종이 권역 밖으로 밀려나는 거리'다.")
    else:
        print("※ 권역에 수용 '확인(Y)' 병원이 0곳 — 전 후보가 미보고.")
        print("  구급대원은 전화 확인 외 선택지가 없다 = 뺑뺑이 메커니즘.")


def selftest():
    # haversine 정합성: 세종시청~대전 약 25~35km 범위 확인
    d = haversine(36.4800, 127.2890, 36.3504, 127.3845)  # 세종~대전 근방
    assert 10 < d < 60, d
    # bed_score: 응급실 가중이 더 크다
    assert bed_score({"hvec": "10", "hvoc": "0", "hvicc": "0"}) == 20
    assert bed_score({"hvec": "-3", "hvoc": "2"}) == 2  # 음수는 0 처리
    assert bed_score({}) is None
    # origins 폴백
    o, w = origins()
    assert len(o) == len(SEJONG_DONG) and abs(sum(x[3] for x in o) - 1) < 1e-6
    print(f"[selftest] OK — haversine={d:.1f}km, 출발점{len(o)}개, "
          f"인구가중={'예' if w else '균등폴백'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--db", default="egen_snapshots.db")
    ap.add_argument("--symptom", default="뇌출혈")
    ap.add_argument("--origin", default=None)
    ap.add_argument("--topn", type=int, default=12)
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        try:
            route(a.db, a.symptom, a.origin, a.topn)
        except KeyError as e:
            print("오류:", e)
            sys.exit(1)


if __name__ == "__main__":
    main()