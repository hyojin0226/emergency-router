#!/usr/bin/env python3
"""verify_backtest.py — 로컬 backtest.py가 권역필터 패치본인지 확인

사용:  python verify_backtest.py
"""
import importlib, sys

try:
    import backtest as bt
except Exception as e:  # noqa
    sys.exit(f"backtest import 실패: {e}")
importlib.reload(bt)

ok = True

if hasattr(bt, "REGION_SIDO"):
    print(f"✓ REGION_SIDO 있음: {bt.REGION_SIDO}")
    if set(bt.REGION_SIDO) != {"세종특별자치시", "대전광역시",
                               "충청남도", "충청북도"}:
        print("  ✗ 권역 구성이 예상과 다름")
        ok = False
else:
    print("✗ REGION_SIDO 없음 → 옛 backtest.py. 새 파일로 덮어써라.")
    ok = False

# load() 소스에 sido 필터가 실제로 들어갔는지
import inspect
src = inspect.getsource(bt.load)
if "sido IN" in src:
    print("✓ load() 에 권역 sido 필터 적용됨")
else:
    print("✗ load() 에 권역 필터 없음 → 옛 버전")
    ok = False

print("\n결론:",
      "권역필터 패치본 정상 — backtest 재실행하면 현실 수치"
      if ok else
      "옛 버전 — 내가 공유한 새 backtest.py 로 덮어써라")
sys.exit(0 if ok else 1)