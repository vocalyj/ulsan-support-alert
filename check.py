# -*- coding: utf-8 -*-
"""울산 지원사업 공고 알림 프로그램
- 여러 게시판을 브라우저로 열어서 글 제목을 읽고
- 키워드에 맞는 '새 글'이 있으면 텔레그램으로 알려 줍니다.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

# ------------------------------------------------------------------
# 1. 확인할 게시판 목록 (주소를 추가하려면 아래 형식으로 한 줄 더 적으면 됩니다)
# ------------------------------------------------------------------
SOURCES = [
    {"name": "울산문화관광재단 공모 및 입찰", "url": "https://uctf.or.kr/board/bid?page=0"},
    {"name": "울산문화24 공지사항", "url": "https://ulsanculture.kr/webuser/notice/list.html"},
    {"name": "울산문화예술회관 공지사항", "url": "https://www.ulsan.go.kr/ucac/art/page.do?mnu_code=mnu003001"},
    {"name": "문화포털 문화지원사업", "url": "https://www.culture.go.kr/portal/cltBnf/cltSup/list.do?menuNo=200104"},
    {"name": "문화포털 보조금지원사업", "url": "https://www.culture.go.kr/portal/cltBnf/rcrtInfo/userList.do?menuNo=200068"},
]

# ------------------------------------------------------------------
# 2. 키워드 (제목에 이 중 하나라도 있으면 알림 후보, 제외어가 있으면 제외)
# ------------------------------------------------------------------
INCLUDE = [
    "지원", "공모", "모집", "보조금", "예술단체", "참가단체", "예술인", "창작",
    "출연", "공연", "음악", "성악", "클래식", "중창", "합창",
]
EXCLUDE = [
    "채용", "인턴", "관광기업", "기념품", "입찰", "결과 발표", "결과발표",
    "심의 결과", "심의결과", "단원", "대관",
]

MAX_MESSAGES_PER_RUN = 10      # 한 번에 보낼 최대 알림 수
STATE_FILE = Path("seen.json")  # 이미 본 글을 기억하는 파일
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

DATE_RE = re.compile(r"\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}\.?")
STATUS_RE = re.compile(
    r"\bD\s*[-+]\s*\d+\b|\bNEW\b|\bnew\b|접수중|접수 중|접수예정|접수마감|마감임박|모집중|모집 중|마감|진행중|종료"
)


def clean_title(text):
    """화면 글자에서 날짜·상태 표시를 빼고 정리한 제목 (같은 글인지 비교할 때 사용)"""
    t = DATE_RE.sub(" ", text)
    t = STATUS_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def display_title(text):
    t = re.sub(r"\s+", " ", text).strip()
    return t if len(t) <= 120 else t[:117] + "..."


def matches(title):
    if any(x in title for x in EXCLUDE):
        return False
    return any(x in title for x in INCLUDE)


def make_key(source_name, title):
    return source_name + "|" + clean_title(title)[:50]


def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            data.setdefault("init", {})
            data.setdefault("seen", [])
            data.setdefault("last_heartbeat", "")
            data.setdefault("week_alerts", 0)
            return data
        except Exception:
            pass
    return {"init": {}, "seen": [], "last_heartbeat": "", "week_alerts": 0}


def save_state(state):
    state["seen"] = state["seen"][-3000:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=30) as r:
                return r.status == 200
        except Exception as e:  # 네트워크 오류 시 다시 시도
            print("텔레그램 전송 오류:", type(e).__name__, str(e)[:100].replace(token, "***"))
    return False


def fetch_links(context, url):
    """게시판을 브라우저로 열어서 (글자, 주소) 목록을 읽어 옵니다."""
    page = context.new_page()
    try:
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception:
            page.goto(url, wait_until="load", timeout=60000)
        page.wait_for_timeout(3000)
        raw = page.eval_on_selector_all(
            "a",
            "els => els.map(e => ({t: (e.innerText || e.textContent || '').trim(),"
            " h: e.getAttribute('href') || ''}))",
        )
    finally:
        page.close()
    links = []
    for item in raw:
        text = re.sub(r"\s+", " ", item.get("t", "")).strip()
        if not (6 <= len(text) <= 250):
            continue
        href = item.get("h", "")
        if href.startswith("javascript") or href.startswith("#") or not href:
            link = url
        else:
            link = urllib.parse.urljoin(url, href)
        links.append((text, link))
    return links


def short_err(e):
    lines = str(e).strip().splitlines()
    first = lines[0] if lines else ""
    return (first[:160] or type(e).__name__)


def main():
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    test_mode = os.environ.get("TEST_MODE", "").strip().lower() == "true"
    if not token or not chat_id:
        print("오류: TELEGRAM_TOKEN 또는 TELEGRAM_CHAT_ID 가 설정되지 않았습니다.")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    state = load_state()
    seen = set(state["seen"])
    alerts = []   # (출처, 제목, 주소)
    report = []   # 테스트 보고용

    details = []  # 테스트 보고용: (게시판 이름, 키워드에 맞는 글 제목들)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale="ko-KR", user_agent=USER_AGENT)
        browser2 = None
        context2 = None
        for src in SOURCES:
            name, url = src["name"], src["url"]
            links = None
            err1 = ""
            try:
                links = fetch_links(context, url)
            except Exception as e:
                err1 = short_err(e)
            if links is None:
                # 한 번 더: HTTP/2를 끄고 다시 시도
                try:
                    if browser2 is None:
                        browser2 = p.chromium.launch(args=["--disable-http2"])
                        context2 = browser2.new_context(locale="ko-KR", user_agent=USER_AGENT)
                    links = fetch_links(context2, url)
                except Exception as e:
                    msg = f"{name}: 열지 못했습니다\n  1차: {err1}\n  2차: {short_err(e)}"
                    print(msg)
                    report.append(msg)
                    continue

            matched = []
            keys_in_page = set()
            for text, link in links:
                if not matches(text):
                    continue
                key = make_key(name, text)
                if key in keys_in_page:
                    continue
                keys_in_page.add(key)
                matched.append((display_title(text), link, key))

            print(f"[{name}] 링크 {len(links)}개, 키워드에 맞는 글 {len(matched)}개")
            for t, _, _ in matched[:30]:
                print("   -", t)
            report.append(f"{name}\n  링크 {len(links)}개 / 키워드에 맞는 글 {len(matched)}개")
            details.append((name, [t for t, _, _ in matched]))

            if name not in state["init"]:
                # 처음 확인하는 게시판: 지금 있는 글은 기억만 하고 알리지 않음
                if links:
                    for _, _, key in matched:
                        seen.add(key)
                    state["init"][name] = True
                continue

            for title, link, key in matched:
                if key not in seen:
                    seen.add(key)
                    alerts.append((name, title, link))
        browser.close()
        if browser2 is not None:
            browser2.close()

    # ---- 알림 보내기 ----
    sent = 0
    for name, title, link in alerts[:MAX_MESSAGES_PER_RUN]:
        text = f"🔔 새 공고\n[{name}]\n{title}\n{link}"
        if send_telegram(token, chat_id, text):
            sent += 1
    if len(alerts) > MAX_MESSAGES_PER_RUN:
        send_telegram(token, chat_id, f"그 밖에 새 공고 {len(alerts) - MAX_MESSAGES_PER_RUN}건이 더 있습니다. 게시판을 직접 확인해 주세요.")
    state["week_alerts"] += len(alerts)

    # ---- 테스트 모드: 확인 결과를 텔레그램으로 보고 ----
    if test_mode:
        body = "✅ 테스트 실행 결과\n(처음 실행이라 지금 올라온 글은 알리지 않고 기억만 합니다)\n\n" + "\n\n".join(report)
        send_telegram(token, chat_id, body[:3900])
        for dname, titles in details:
            lines = "\n".join(f"{i + 1}. {t[:70]}" for i, t in enumerate(titles[:25]))
            send_telegram(token, chat_id, f"[{dname}] 키워드에 맞는 글 목록\n{lines}"[:3900])

    # ---- 일주일에 한 번 '정상 작동 중' 알림 ----
    today = date.today().isoformat()
    last = state.get("last_heartbeat", "")
    if not last:
        state["last_heartbeat"] = today
    else:
        try:
            days = (date.fromisoformat(today) - date.fromisoformat(last)).days
        except Exception:
            days = 7
        if days >= 7:
            send_telegram(token, chat_id, f"✅ 알림 프로그램이 정상 작동 중입니다.\n지난 7일간 새 공고 알림: {state['week_alerts']}건")
            state["last_heartbeat"] = today
            state["week_alerts"] = 0

    state["seen"] = sorted(seen)
    save_state(state)
    print(f"완료: 새 공고 {len(alerts)}건, 전송 {sent}건")


if __name__ == "__main__":
    main()
