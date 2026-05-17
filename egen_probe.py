#!/usr/bin/env python3
"""
egen_probe.py  —  E-Gen API 단일 호출 진단기

목적: 실제 응답을 눈으로 확인해서 (1) 오퍼레이션명이 맞는지
      (2) 중증질환 수용가능 필드명이 실제로 무엇인지 30초 안에 확정한다.

사용:
    export EGEN_SERVICE_KEY='발급받은_디코딩_키'
    python egen_probe.py --op severe --sido 대전광역시
    python egen_probe.py --op beds   --sido 세종특별자치시
    python egen_probe.py --op list   --sido 충청북도

키가 인코딩 키밖에 없으면 --encoded 추가.
"""
import argparse, os, sys, urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "http://apis.data.go.kr/B552657/ErmctInfoInqireService"

# 오퍼레이션명: 내 지식 기준값. 이 스크립트로 살아있는지 직접 확인하라.
OPS = {
    "beds":   "getEmrrmRltmUsefulSckbdInfoInqire",   # 응급실 실시간 가용병상
    "severe": "getSrsillDissAceptncPosblInfoInqire",  # 중증질환 수용가능정보 (핵심)
    "list":   "getEgytListInfoInqire",                # 응급의료기관 목록(좌표 포함)
}


def call(op_key: str, sido: str, key: str, encoded: bool) -> str:
    op = OPS[op_key]
    params = {"STAGE1": sido, "pageNo": "1", "numOfRows": "100"}
    qs = urllib.parse.urlencode(params)
    if encoded:
        # 이미 인코딩된 키는 다시 인코딩하면 안 됨 → 직접 붙인다
        url = f"{BASE}/{op}?serviceKey={key}&{qs}"
    else:
        url = f"{BASE}/{op}?serviceKey={urllib.parse.quote(key)}&{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "egen-probe/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--op", choices=list(OPS), default="severe")
    ap.add_argument("--sido", default="대전광역시")
    ap.add_argument("--encoded", action="store_true",
                    help="키가 인코딩(Encoding) 키일 때 지정")
    args = ap.parse_args()

    key = os.environ.get("EGEN_SERVICE_KEY", "").strip()
    if not key:
        sys.exit("환경변수 EGEN_SERVICE_KEY 가 비어 있음.")

    print(f"[호출] op={OPS[args.op]}  STAGE1={args.sido}\n")
    raw = call(args.op, args.sido, key, args.encoded)

    # 흔한 에러 패턴 먼저 잡아주기
    low = raw.lower()
    if "service_key_is_not_registered" in low or "serviceregistration" in low:
        print("!! SERVICE KEY 미등록 에러.")
        print("   → 키 종류 확인: requests/urllib에는 보통 '디코딩' 키를 쓴다.")
        print("   → 인코딩 키만 있으면 --encoded 옵션으로 다시 실행.")
        print("   → 또는 신청 직후라면 활성화에 수십분~1시간 걸릴 수 있음.\n")
    if "limited_number_of_service_requests" in low:
        print("!! 일일 호출 한도 초과.\n")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        print("XML 파싱 실패. 원문 일부:\n", raw[:1500])
        return

    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    msg = root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") \
          or root.findtext(".//errMsg")
    total = root.findtext(".//totalCount")
    print(f"resultCode={code}  msg={msg}  totalCount={total}")

    items = root.findall(".//item")
    print(f"item 개수(이 페이지)={len(items)}\n")
    if not items:
        print("item 없음. 원문 앞부분:\n", raw[:1200])
        return

    first = items[0]
    print("=== 첫 번째 item의 실제 필드명 / 값 (★ 이걸 그대로 기록해둬라) ===")
    for child in first:
        val = (child.text or "").strip()
        print(f"  {child.tag:<18} = {val}")

    if args.op == "severe":
        print("\n★ 위에서 MKioskTy* 또는 유사 코드가 중증질환 항목이다.")
        print("  어떤 코드가 뇌출혈수술/재관류중재술/소아응급인지 메모해서")
        print("  collector의 SEVERE_FIELD_MAP 에 채워라.")


if __name__ == "__main__":
    main()