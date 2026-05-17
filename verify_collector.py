#!/usr/bin/env python3
"""verify_collector.py — 로컬 egen_collector.py가 패치 버전인지 확인

사용:  python verify_collector.py
"""
import importlib, inspect, sys

try:
    import egen_collector as ec
except Exception as e:  # noqa
    sys.exit(f"egen_collector import 실패: {e}")

importlib.reload(ec)

ok = True

# 1) 패치 핵심: paginate_nationwide 존재
if hasattr(ec, "paginate_nationwide"):
    sig = list(inspect.signature(ec.paginate_nationwide).parameters)
    print(f"✓ paginate_nationwide 있음  시그니처={sig}")
    if sig != ["op", "key", "encoded"]:
        print("  ✗ 시그니처가 예상과 다름 → 파일이 최신 패치 아님")
        ok = False
else:
    print("✗ paginate_nationwide 없음 → 옛 버전. 새 파일로 교체 필요.")
    ok = False

# 2) upsert_hospitals 새 시그니처 (con, items) — 옛 버전은 (con, sido, items)
if hasattr(ec, "upsert_hospitals"):
    sig = list(inspect.signature(ec.upsert_hospitals).parameters)
    print(f"✓ upsert_hospitals 시그니처={sig}")
    if sig != ["con", "items"]:
        print("  ✗ 옛 시그니처(con,sido,items) → 파일이 최신 패치 아님")
        ok = False
else:
    print("✗ upsert_hospitals 없음")
    ok = False

# 3) 주소기반 시도추출 헬퍼
print("✓ _sido_from_addr 있음" if hasattr(ec, "_sido_from_addr")
      else "✗ _sido_from_addr 없음 → 옛 버전")
ok = ok and hasattr(ec, "_sido_from_addr")

print("\n결론:", "패치 버전 정상 — fetch_master_once.py 실행 가능"
      if ok else
      "옛 버전 — 내가 공유한 새 egen_collector.py 로 덮어써라")
sys.exit(0 if ok else 1)