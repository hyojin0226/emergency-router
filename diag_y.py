#!/usr/bin/env python3
"""diag_y.py — phone_rate=0 이 진짜인지 확정

질문: B의 전화확인 의존이 0% 인 건
  (a) 권역에 Y가 충분해서 항상 직행 가능 (정상, 가설 재구성 필요)
  (b) 또 다른 버그 (Y 판정 오류)
   중 무엇인가?

세종 시내 Y 수 vs 권역 전체 Y 수를 증상별로 직접 센다.
사용:  python diag_y.py            (기본 뇌출혈)
       python diag_y.py 심근경색
"""
import sqlite3, json, sys
import severe_routing as sr

DB = "egen_snapshots.db"
SYM = sys.argv[1] if len(sys.argv) > 1 else "뇌출혈"
SEJONG = "세종특별자치시"
REGION = (SEJONG, "대전광역시", "충청남도", "충청북도")

con = sqlite3.connect(DB)
ts = con.execute("SELECT MAX(snapshot_ts) FROM severe_accept").fetchone()[0]
rows = con.execute(
    "SELECT hpid,duty_name,payload_json FROM severe_accept "
    "WHERE snapshot_ts=?", (ts,)).fetchall()

# hpid -> sido (hospitals 주소기반)
sido = {}
for hpid, s in con.execute(
        "SELECT hpid,sido FROM hospitals WHERE sido IS NOT NULL"):
    sido[hpid] = s

reg = {"Y": 0, "UNREPORTED": 0, "N": 0}
sej = {"Y": 0, "UNREPORTED": 0, "N": 0}
sej_names = {"Y": [], "UNREPORTED": [], "N": []}

for hpid, name, pj in rows:
    s = sido.get(hpid)
    if s not in REGION:
        continue
    st, _ = sr.hospital_state_for_symptom(json.loads(pj), SYM)
    reg[st] += 1
    if s == SEJONG:
        sej[st] += 1
        sej_names[st].append(name)

print(f"[{SYM}] 스냅샷 {ts}\n")
print(f"권역(세종+대전+충남+충북):  ✅Y {reg['Y']}  "
      f"☎️미보고 {reg['UNREPORTED']}  ⛔N {reg['N']}")
print(f"세종 시내만           :  ✅Y {sej['Y']}  "
      f"☎️미보고 {sej['UNREPORTED']}  ⛔N {sej['N']}")
print(f"\n세종 시내 병원 상태:")
for k in ("Y", "UNREPORTED", "N"):
    if sej_names[k]:
        print(f"  {k}: {', '.join(sej_names[k])}")

print("\n판정:")
if reg["Y"] >= 1 and sej["Y"] == 0:
    print("  → (a) 정상. 권역엔 Y가 있으나 '세종 시내 Y=0'.")
    print("     phone_rate 0% 는 버그 아님 — B가 권역 Y로 직행하기 때문.")
    print("     ★ 핵심 지표는 phone_rate 가 아니라 '세종시내 Y=0'")
    print("       + A의 허탕·시간손실. 가설은 이 형태로 재구성한다.")
elif reg["Y"] == 0:
    print("  → Y가 권역에도 0. phone_rate 0% 는 버그(분기 미작동).")
else:
    print("  → 세종 시내에도 Y 존재. 서사 재검토 필요.")