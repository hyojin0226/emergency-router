#!/usr/bin/env python3
"""
fetch_master_once.py — 병원 좌표(마스터)만 지금 단발 수집

목적: 백테스트가 비현실적(4.3km)인 원인 = hospitals 좌표 부족.
      수집기를 안 멈추고, 전국 목록을 1회 받아 hospitals 채운다.
      (패치된 egen_collector 의 paginate_nationwide / upsert_hospitals
       를 그대로 재사용 — 동일 로직 보장)

사용:
    export EGEN_SERVICE_KEY='디코딩_키'        # (Windows) set EGEN_SERVICE_KEY=...
    python fetch_master_once.py                 # 디코딩 키
    python fetch_master_once.py --encoded       # 인코딩 키

실행 후 점검:
    python -c "import sqlite3;c=sqlite3.connect('egen_snapshots.db');print(c.execute('SELECT COUNT(*) FROM hospitals WHERE lat IS NOT NULL').fetchone())"
"""
import argparse, os, sqlite3, sys
import egen_collector as ec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoded", action="store_true")
    ap.add_argument("--db", default=os.environ.get("EGEN_DB",
                                                   "egen_snapshots.db"))
    ap.add_argument("--no-rebuild", action="store_true",
                    help="기본은 hospitals 비우고 새로(옛 버그 sido 정리). "
                         "이 옵션은 덮어쓰기만.")
    a = ap.parse_args()

    key = os.environ.get("EGEN_SERVICE_KEY", "").strip()
    if not key:
        sys.exit("환경변수 EGEN_SERVICE_KEY 비어있음.")

    con = sqlite3.connect(a.db)
    ec.init_db(con)  # 테이블 없으면 생성(있으면 무해)

    before = con.execute(
        "SELECT COUNT(*) FROM hospitals WHERE lat IS NOT NULL").fetchone()[0]
    # 진단에서 확인된 옛 버그: 전국 병원이 단일 sido로 잘못 태깅됨.
    # hospitals 만 비운다(중증/병상 스냅샷은 건드리지 않음).
    if not a.no_rebuild:
        con.execute("DELETE FROM hospitals")
        con.commit()
        print(f"수집 전 좌표 보유: {before}  → hospitals 초기화"
              f"(스냅샷 테이블은 보존)")
    else:
        print(f"수집 전 좌표 보유 병원: {before} (덮어쓰기 모드)")
    print("전국 목록 페이지네이션 수집 중... (533+건, 수십 초)")

    items, err = ec.paginate_nationwide(ec.OP_LIST, key, a.encoded)
    if err:
        sys.exit(f"수집 실패: {err}\n"
                 f"→ 키 종류(디코딩/인코딩) 또는 --encoded 확인.")

    ec.upsert_hospitals(con, items)
    con.commit()

    after = con.execute(
        "SELECT COUNT(*) FROM hospitals WHERE lat IS NOT NULL").fetchone()[0]
    tot = con.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
    print(f"수집 완료: 전국 {len(items)}건 처리")
    print(f"좌표 보유 병원: {before} → {after}  (hospitals 총 {tot})")

    # 권역 4개 시도에 좌표가 실제로 붙었는지 확인
    print("\n권역 좌표 점검(주소 앞단어 기준):")
    for sido in ("세종특별자치시", "대전광역시", "충청남도", "충청북도"):
        n = con.execute(
            "SELECT COUNT(*) FROM hospitals "
            "WHERE lat IS NOT NULL AND sido=?", (sido,)).fetchone()[0]
        print(f"  {sido}: {n}곳")
    con.close()
    print("\n→ 이제 backtest.py 를 다시 실행하면 권역 전체로 평가된다.")


if __name__ == "__main__":
    main()