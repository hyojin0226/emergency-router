#!/usr/bin/env python3
"""
case_mix.py — 백테스트 가상환자의 증상 구성 (응급의료 통계연보 기반)

설계: 통계연보의 중증응급질환 분류는 우리 28개 mkioskty 코드와
1:1이 아니다. 그래서 (통계연보 질환 → router 증상키) 매핑을 거친다.
통계연보엔 있으나 mkioskty 라우팅 대상이 아닌 항목(예: 중증외상,
심정지)은 백테스트에서 제외하고 '한계'로 명시한다.

★ 네가 할 일 (5~10분): 응급의료 통계연보 / 응급의료 통계포털에서
   중증응급질환별 발생(또는 내원) '건수'를 찾아 COUNT 에 채운다.
   전국 또는 충청권 단위면 충분(세종 단독 수치 없어도 됨 → 발표에 명시).
   숫자를 안 채우면(전부 0) 백테스트가 '균등 발생'으로 폴백한다.
"""

# 통계연보 질환명 : (router 증상키, 통계연보 발생건수)
#   - 라우팅 가능 키: severe_routing.SYMPTOM_TO_CODES 참조
#   - COUNT 0 = 미입력
# 2024년 응급의료통계포털 '질환별 중증응급환자 구성'
# 기준: 환자 주소지 기준, 세종+대전+충남+충북 합산
# 뇌출혈 = 뇌실질출혈 + 거미막하출혈
# 고위험분만 = 주산기질환 대체 사용
STAT_DISEASE = {
    "급성심근경색":  ("심근경색",  4031),
    "뇌출혈":        ("뇌출혈",    3974),
    "뇌경색":        ("뇌경색",    11166),
    "대동맥응급":    ("대동맥응급", 481),
    "고위험분만":    ("고위험분만", 1178),
    "중증화상":      ("중증화상",  12),
}


def case_mix(equal_if_blank=True):
    """[(router_symptom, prob), ...] 반환. 합=1.
    COUNT 합이 0이면 균등 폴백."""
    total = sum(c for _, c in STAT_DISEASE.values())
    out = []
    if total > 0:
        for _, (sym, c) in STAT_DISEASE.items():
            if c > 0:
                out.append((sym, c / total))
    elif equal_if_blank:
        syms = [sym for _, (sym, _) in STAT_DISEASE.items()]
        for s in syms:
            out.append((s, 1.0 / len(syms)))
    return out, (total > 0)


if __name__ == "__main__":
    mix, weighted = case_mix()
    print(f"증상 {len(mix)}종 / 통계기반={'예' if weighted else '아니오(균등 폴백)'}")
    for sym, p in mix:
        print(f"  {sym:10s} {p*100:5.1f}%")
    if not weighted:
        print("\n※ case_mix.py 의 COUNT 를 통계연보 수치로 채우면 자동 전환.")