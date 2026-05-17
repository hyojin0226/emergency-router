#!/usr/bin/env python3
"""diag.py — 데이터 상태 진단 (PowerShell 따옴표 문제 회피용)

사용:  python diag.py
"""
import sqlite3, os, sys

DB = os.environ.get("EGEN_DB", "egen_snapshots.db")
if not os.path.exists(DB):
    sys.exit(f"DB 없음: {DB}  (수집기 실행 폴더에서 돌려라)")

c = sqlite3.connect(DB)

tot = c.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
geo = c.execute(
    "SELECT COUNT(*) FROM hospitals "
    "WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchone()[0]
print(f"hospitals 총 {tot}곳 / 좌표 보유 {geo}곳")

print("\n권역 4개 시도 좌표 보유 수:")
for sido in ("세종특별자치시", "대전광역시", "충청남도", "충청북도"):
    n = c.execute(
        "SELECT COUNT(*) FROM hospitals "
        "WHERE lat IS NOT NULL AND sido=?", (sido,)).fetchone()[0]
    print(f"  {sido}: {n}")

print("\n최근 '전국' 마스터 수집 로그:")
rows = c.execute(
    "SELECT ts, n_items, ok, note FROM poll_log "
    "WHERE sido='전국' ORDER BY ts DESC LIMIT 5").fetchall()
if not rows:
    print("  (없음) → 패치된 collector로 재시작한 적이 없거나 "
          "마스터 주기(기본 6h)가 아직 안 옴.")
else:
    for ts, n, ok, note in rows:
        print(f"  {ts}  n={n}  ok={ok}  note={note}")

print("\n중증/병상 스냅샷 누적:")
for t in ("severe_accept", "er_beds"):
    r = c.execute(
        f"SELECT COUNT(*), MIN(snapshot_ts), MAX(snapshot_ts) "
        f"FROM {t}").fetchone()
    print(f"  {t}: {r[0]}행  {r[1]} ~ {r[2]}")

print("\n진단:")
if geo < 30:
    print("  ✗ 좌표 보유 병원 부족 → 백테스트가 소수 병원만 평가해")
    print("    거리·전화확인율이 비현실적. fetch_master_once.py 실행 필요.")
else:
    print("  ✓ 좌표 충분. 백테스트 비현실 원인은 다른 곳 → 결과 공유.")