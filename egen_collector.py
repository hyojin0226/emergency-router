#!/usr/bin/env python3
"""
egen_collector.py  —  E-Gen 권역 스냅샷 수집기 (세종+대전+충남+충북)

핵심 설계 원칙
  - payload_json 으로 item의 '모든' 필드를 통째로 저장한다.
    → 중증질환 필드명이 무엇이든, API가 바뀌어도 데이터를 잃지 않는다.
    → 정밀 파싱은 probe로 필드명 확인 후 나중에 SQL로 한다.
  - 실시간 2종(병상/중증수용)은 interval마다, 병원 마스터는 가끔만 수집.
  - 한 시도/페이지에서 에러나도 루프 전체가 죽지 않는다(다음으로 진행).

사용
    export EGEN_SERVICE_KEY='디코딩_키'
    pip install requests
    python egen_collector.py                 # 5분 주기로 무한 수집
    python egen_collector.py --once          # 1회만
    python egen_collector.py --interval 600  # 10분 주기
    python egen_collector.py --encoded       # 인코딩 키일 때
    python egen_collector.py --selftest      # 네트워크 없이 파서/DB 검증

권장: tmux/screen 안에서 실행해 끊기지 않게 두고, 며칠치를 쌓는다.
"""
import argparse, json, os, sqlite3, sys, time, traceback
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None  # selftest는 requests 없이도 동작

BASE = "http://apis.data.go.kr/B552657/ErmctInfoInqireService"

# ── 권역 범위 (probe로 어떤 표기가 데이터가 나오는지 확인 후 확정) ──
SIDO_LIST = ["세종특별자치시", "대전광역시", "충청남도", "충청북도"]

# ── 오퍼레이션 (probe로 살아있는지 확인한 값으로 맞춰라) ──
OP_BEDS   = "getEmrrmRltmUsefulSckbdInfoInqire"   # 응급실 실시간 가용병상
OP_SEVERE = "getSrsillDissAceptncPosblInfoInqire"  # 중증질환 수용가능 (핵심)
OP_LIST   = "getEgytListInfoInqire"                # 병원 목록(좌표)

# 병상 응답에서 분석에 자주 쓰는 코드(편의 추출). 의미는 probe/문서로 확인.
# 없으면 그냥 NULL 들어가고, 어차피 payload_json에 원본 다 있음.
BEDS_CONVENIENCE = ["hpid", "dutyName", "hvidate", "hvec", "hvoc",
                    "hvcc", "hvncc", "hvccc", "hvgc"]

NUM_ROWS = 100
TIMEOUT = 20
RETRY = 3
DB_PATH = os.environ.get("EGEN_DB", "egen_snapshots.db")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ────────────────────────── DB ──────────────────────────
def init_db(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS hospitals(
        hpid TEXT PRIMARY KEY, duty_name TEXT, sido TEXT, addr TEXT,
        tel TEXT, er_tel TEXT, lat REAL, lon REAL, last_seen TEXT,
        payload_json TEXT
    );
    CREATE TABLE IF NOT EXISTS er_beds(
        snapshot_ts TEXT, sido TEXT, hpid TEXT, duty_name TEXT,
        hvidate TEXT, hvec TEXT, hvoc TEXT, hvcc TEXT, hvncc TEXT,
        hvccc TEXT, hvgc TEXT, payload_json TEXT
    );
    CREATE TABLE IF NOT EXISTS severe_accept(
        snapshot_ts TEXT, sido TEXT, hpid TEXT, duty_name TEXT,
        hvidate TEXT, payload_json TEXT
    );
    CREATE TABLE IF NOT EXISTS poll_log(
        ts TEXT, op TEXT, sido TEXT, page INTEGER,
        n_items INTEGER, ok INTEGER, note TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_beds_ts   ON er_beds(snapshot_ts);
    CREATE INDEX IF NOT EXISTS ix_sev_ts    ON severe_accept(snapshot_ts);
    CREATE INDEX IF NOT EXISTS ix_beds_hpid ON er_beds(hpid);
    CREATE INDEX IF NOT EXISTS ix_sev_hpid  ON severe_accept(hpid);
    """)
    con.commit()


# ───────────────────── HTTP + 파싱 ─────────────────────
def fetch(op, sido, page, key, encoded):
    params = {"STAGE1": sido, "pageNo": str(page), "numOfRows": str(NUM_ROWS)}
    if encoded:
        url = f"{BASE}/{op}?serviceKey={key}"
        send_params = params
    else:
        url = f"{BASE}/{op}"
        send_params = dict(params, serviceKey=key)
    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            r = requests.get(url, params=send_params, timeout=TIMEOUT,
                             headers={"User-Agent": "egen-collector/1.0"})
            if r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            return r.text
        except Exception as e:  # noqa
            last_err = e
            time.sleep(2 * attempt)
    raise last_err


def parse_items(xml_text):
    """ElementTree로 item 전부를 dict 리스트로. 에러면 (None, reason)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, "xml_parse_error", None
    code = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode")
    if code not in (None, "", "00"):
        msg = (root.findtext(".//resultMsg")
               or root.findtext(".//returnAuthMsg")
               or root.findtext(".//errMsg") or "")
        return None, f"api_error code={code} {msg}", None
    total = root.findtext(".//totalCount")
    items = []
    for it in root.findall(".//item"):
        d = {}
        for ch in it:
            d[ch.tag] = (ch.text or "").strip()
        items.append(d)
    try:
        total = int(total) if total else None
    except ValueError:
        total = None
    return items, None, total


def paginate(op, sido, key, encoded):
    """totalCount 기준으로 전 페이지 수집."""
    all_items, page = [], 1
    while True:
        xml_text = fetch(op, sido, page, key, encoded)
        items, err, total = parse_items(xml_text)
        if err:
            return all_items, err
        all_items.extend(items)
        if not items or len(items) < NUM_ROWS:
            break
        if total is not None and len(all_items) >= total:
            break
        page += 1
        if page > 50:  # 안전장치
            break
        time.sleep(0.4)
    return all_items, None


def paginate_nationwide(op, key, encoded):
    """STAGE1 없이 전국을 totalCount 끝까지 수집.
    list API는 시도 필터가 안 먹으므로 전국을 받고, 권역 매칭은
    나중에 beds/severe 의 hpid 로 한다. fetch 는 STAGE1 을 항상
    보내지만 list 응답에선 무시되므로 정확도엔 영향 없다."""
    all_items, page, total = [], 1, None
    seen = set()
    while True:
        xml_text = fetch(op, "전국", page, key, encoded)
        items, err, t = parse_items(xml_text)
        if err:
            return all_items, err
        if t is not None:
            total = t
        for d in items:
            hpid = d.get("hpid")
            if hpid and hpid not in seen:
                seen.add(hpid)
                all_items.append(d)
        if not items or len(items) < NUM_ROWS:
            break
        if total is not None and len(seen) >= total:
            break
        page += 1
        if page > 200:  # 전국이라 안전장치 크게
            break
        time.sleep(0.4)
    return all_items, None
def _sido_from_addr(addr):
    """dutyAddr 앞 토큰에서 시도명 추출. 없으면 None."""
    if not addr:
        return None
    head = addr.strip().split()
    return head[0] if head else None


def upsert_hospitals(con, items):
    rows = []
    for d in items:
        rows.append((
            d.get("hpid"), d.get("dutyName"),
            _sido_from_addr(d.get("dutyAddr")), d.get("dutyAddr"),
            d.get("dutyTel1"), d.get("dutyTel3"),
            _f(d.get("wgs84Lat")), _f(d.get("wgs84Lon")),
            utcnow(), json.dumps(d, ensure_ascii=False),
        ))
    con.executemany("""
        INSERT INTO hospitals(hpid,duty_name,sido,addr,tel,er_tel,lat,lon,
                              last_seen,payload_json)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(hpid) DO UPDATE SET
          duty_name=excluded.duty_name, sido=excluded.sido,
          addr=excluded.addr, tel=excluded.tel, er_tel=excluded.er_tel,
          lat=COALESCE(excluded.lat,hospitals.lat),
          lon=COALESCE(excluded.lon,hospitals.lon),
          last_seen=excluded.last_seen, payload_json=excluded.payload_json
    """, rows)


def insert_beds(con, ts, sido, items):
    rows = [(ts, sido, *[d.get(c) for c in BEDS_CONVENIENCE],
             json.dumps(d, ensure_ascii=False)) for d in items]
    con.executemany("""
        INSERT INTO er_beds(snapshot_ts,sido,hpid,duty_name,hvidate,
                            hvec,hvoc,hvcc,hvncc,hvccc,hvgc,payload_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def insert_severe(con, ts, sido, items):
    rows = [(ts, sido, d.get("hpid"), d.get("dutyName"), d.get("hvidate"),
             json.dumps(d, ensure_ascii=False)) for d in items]
    con.executemany("""
        INSERT INTO severe_accept(snapshot_ts,sido,hpid,duty_name,
                                  hvidate,payload_json)
        VALUES(?,?,?,?,?,?)""", rows)


def log(con, op, sido, page, n, ok, note=""):
    con.execute("INSERT INTO poll_log VALUES(?,?,?,?,?,?,?)",
                (utcnow(), op, sido, page, n, 1 if ok else 0, note))


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ───────────────────── 사이클 ─────────────────────
def run_cycle(con, key, encoded, do_master):
    ts = utcnow()
    print(f"\n=== 사이클 {ts} ===")
    for sido in SIDO_LIST:
        # 실시간: 병상
        try:
            items, err = paginate(OP_BEDS, sido, key, encoded)
            if err:
                print(f"[병상] {sido}: ERROR {err}")
                log(con, OP_BEDS, sido, 0, 0, False, err)
            else:
                insert_beds(con, ts, sido, items)
                log(con, OP_BEDS, sido, 0, len(items), True)
                print(f"[병상] {sido}: {len(items)}건")
        except Exception as e:  # noqa
            print(f"[병상] {sido}: 예외 {e}")
            log(con, OP_BEDS, sido, 0, 0, False, repr(e))

        # 실시간: 중증질환 수용 (핵심)
        try:
            items, err = paginate(OP_SEVERE, sido, key, encoded)
            if err:
                print(f"[중증] {sido}: ERROR {err}")
                log(con, OP_SEVERE, sido, 0, 0, False, err)
            else:
                insert_severe(con, ts, sido, items)
                log(con, OP_SEVERE, sido, 0, len(items), True)
                print(f"[중증] {sido}: {len(items)}건")
        except Exception as e:  # noqa
            print(f"[중증] {sido}: 예외 {e}")
            log(con, OP_SEVERE, sido, 0, 0, False, repr(e))

        time.sleep(0.5)

    # 마스터: 시도 루프 밖에서 전국 1회만. list API는 STAGE1 필터가
    # 안 먹어 매번 전국을 반환하므로, 4번 중복/틀린 sido 박기를 피하고
    # totalCount 끝까지 페이지네이션해 권역 hpid 좌표 룩업을 완성한다.
    if do_master:
        try:
            items, err = paginate_nationwide(OP_LIST, key, encoded)
            if err:
                print(f"[마스터] 전국: ERROR {err}")
                log(con, OP_LIST, "전국", 0, 0, False, err)
            else:
                upsert_hospitals(con, items)
                log(con, OP_LIST, "전국", 0, len(items), True)
                print(f"[마스터] 전국: {len(items)}건 upsert "
                      f"(권역 좌표는 hpid 매칭으로 사용)")
        except Exception as e:  # noqa
            print(f"[마스터] 전국: 예외 {e}")
            log(con, OP_LIST, "전국", 0, 0, False, repr(e))
    con.commit()


# ───────────────────── selftest (네트워크 불필요) ─────────────────────
SAMPLE_SEVERE_XML = """<response><header><resultCode>00</resultCode>
<resultMsg>OK</resultMsg></header><body><items>
<item><hpid>A1100001</hpid><dutyName>샘플권역병원</dutyName>
<hvidate>20260117103000</hvidate><MKioskTy1>Y</MKioskTy1>
<MKioskTy2>N1</MKioskTy2><MKioskTy10>Y</MKioskTy10></item>
</items><numOfRows>100</numOfRows><pageNo>1</pageNo>
<totalCount>1</totalCount></body></response>"""


def selftest():
    print("[selftest] 파서 + DB 적재 경로 검증 (네트워크 미사용)")
    items, err, total = parse_items(SAMPLE_SEVERE_XML)
    assert err is None, err
    assert total == 1 and len(items) == 1, (total, items)
    assert items[0]["MKioskTy1"] == "Y", items[0]
    con = sqlite3.connect(":memory:")
    init_db(con)
    insert_severe(con, utcnow(), "테스트시도", items)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM severe_accept").fetchone()[0]
    payload = con.execute(
        "SELECT payload_json FROM severe_accept").fetchone()[0]
    assert n == 1 and json.loads(payload)["MKioskTy10"] == "Y"
    print(f"[selftest] OK — severe_accept {n}행, payload 보존 확인")
    print("[selftest] 파서가 item의 모든 필드를 보존하므로,")
    print("           실제 중증질환 필드명이 무엇이든 데이터 손실 없음.")


# ───────────────────── main ─────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--encoded", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--master-every", type=int, default=72,
                    help="N 사이클마다 병원 마스터 갱신(기본: 5분*72≈6시간)")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    if requests is None:
        sys.exit("requests 미설치: pip install requests")
    key = os.environ.get("EGEN_SERVICE_KEY", "").strip()
    if not key:
        sys.exit("환경변수 EGEN_SERVICE_KEY 가 비어 있음.")

    con = sqlite3.connect(DB_PATH)
    init_db(con)
    print(f"DB={DB_PATH}  범위={SIDO_LIST}  주기={args.interval}s")

    cycle = 0
    while True:
        do_master = (cycle % max(args.master_every, 1) == 0)
        try:
            run_cycle(con, key, args.encoded, do_master)
        except Exception:  # noqa  사이클 전체 예외도 흡수
            traceback.print_exc()
        cycle += 1
        if args.once:
            break
        time.sleep(args.interval)
    con.close()


if __name__ == "__main__":
    main()