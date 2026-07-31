#!/usr/bin/env python3
"""게시판 신규 글 감시 → 텔레그램 알림.

sites.json 에 등록된 게시판을 확인해서 state.json 에 없는 글만 알린다.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
SITES_FILE = BASE_DIR / "sites.json"
STATE_FILE = BASE_DIR / "state.json"

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
SEEN_LIMIT = 500


# --------------------------------------------------------------------------
# 설정 / 상태
# --------------------------------------------------------------------------
def load_sites() -> list[dict]:
    if not SITES_FILE.exists():
        raise SystemExit(f"설정 파일이 없습니다: {SITES_FILE}")
    with SITES_FILE.open(encoding="utf-8") as fp:
        data = json.load(fp)
    return data.get("sites", [])


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"sites": {}}
    with STATE_FILE.open(encoding="utf-8") as fp:
        data = json.load(fp)
    data.setdefault("sites", {})
    return data


def save_state(state: dict) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as fp:
        json.dump(state, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")


# --------------------------------------------------------------------------
# 텔레그램
# --------------------------------------------------------------------------
def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[warn] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — 발송 생략", file=sys.stderr)
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    for attempt in range(MAX_RETRIES):
        try:
            res = requests.post(TELEGRAM_API.format(token=token), data=payload, timeout=REQUEST_TIMEOUT)
            if res.ok:
                return True
            print(f"[warn] 텔레그램 응답 {res.status_code}: {res.text[:200]}", file=sys.stderr)
        except requests.RequestException as exc:
            print(f"[warn] 텔레그램 발송 실패 ({attempt + 1}/{MAX_RETRIES}): {exc}", file=sys.stderr)
        if attempt < MAX_RETRIES - 1:
            time.sleep(2**attempt)
    return False


def esc(value: str) -> str:
    return html.escape(value or "", quote=False)


def format_post(site_name: str, post: dict) -> str:
    lines = [esc(site_name), f"<b>{esc(post['title'])}</b>"]
    if post.get("date"):
        lines.append(esc(post["date"]))
    if post.get("url"):
        lines.append(esc(post["url"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 수집
# --------------------------------------------------------------------------
def fetch(url: str) -> requests.Response:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            res = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            res.raise_for_status()
            return res
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(2**attempt)
    raise RuntimeError(f"{url} 요청 실패: {last_exc}")


def make_id(site: dict, href: str, title: str, guid: str = "") -> str:
    strategy = site.get("id_strategy") or "href_hash"

    if strategy == "rss_guid":
        source = guid or href or title
        return source.strip() or hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]

    if strategy == "href_param":
        param = site.get("id_param") or "id"
        match = re.search(rf"[?&]{re.escape(param)}=([^&#]+)", href or "")
        if match:
            return match.group(1)
        # 파라미터를 못 찾으면 href 해시로 폴백
        strategy = "href_hash"

    if strategy == "title_hash":
        return hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]

    return hashlib.sha1((href or title).encode("utf-8")).hexdigest()[:16]


def clean_title(site: dict, raw: str, date: str) -> str:
    title = raw or ""
    for token in site.get("title_strip") or []:
        if token:
            title = title.replace(token, " ")
    if date:
        title = title.replace(date, " ")
    return re.sub(r"\s+", " ", title).strip()


def extract_date(site: dict, *texts: str) -> str:
    pattern = site.get("date_regex")
    if not pattern:
        return ""
    for text in texts:
        if not text:
            continue
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def parse_html_site(site: dict) -> list[dict]:
    res = fetch(site["url"])
    res.encoding = res.apparent_encoding or res.encoding
    soup = BeautifulSoup(res.text, "html.parser")

    selector = site.get("item_selector")
    if not selector:
        raise RuntimeError("item_selector 가 비어 있습니다")

    posts: list[dict] = []
    seen_ids: set[str] = set()
    for node in soup.select(selector):
        href = node.get("href") or ""
        if href.strip().lower().startswith("javascript:") or href.strip() == "#":
            href = ""

        anchor_text = node.get_text(" ", strip=True)
        parent = node.find_parent(["tr", "li", "article", "div"])
        parent_text = parent.get_text(" ", strip=True) if parent else ""

        date = extract_date(site, anchor_text, parent_text)

        title_attr = site.get("title_attr")
        raw_title = node.get(title_attr) if title_attr else None
        if not raw_title:
            raw_title = anchor_text
        title = clean_title(site, raw_title, date)
        if not title:
            continue

        url = urljoin(res.url, href) if href else res.url
        post_id = make_id(site, href, title)
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        posts.append({"id": post_id, "title": title, "date": date, "url": url})

    return posts


def parse_rss_site(site: dict) -> list[dict]:
    import feedparser

    res = fetch(site["url"])
    feed = feedparser.parse(res.content)

    posts: list[dict] = []
    seen_ids: set[str] = set()
    for entry in feed.entries:
        title = clean_title(site, entry.get("title", ""), "")
        if not title:
            continue
        link = entry.get("link", "") or ""
        guid = entry.get("id", "") or entry.get("guid", "") or ""
        date = entry.get("published", "") or entry.get("updated", "") or ""
        if site.get("date_regex"):
            date = extract_date(site, date) or date
        post_id = make_id(site, link, title, guid)
        if post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        posts.append({"id": post_id, "title": title, "date": date, "url": link})

    return posts


def parse_site(site: dict) -> list[dict]:
    mode = (site.get("mode") or "html").lower()
    if mode == "rss":
        return parse_rss_site(site)
    if mode == "html":
        return parse_html_site(site)
    raise RuntimeError(f"알 수 없는 mode: {mode}")


def matches_keywords(site: dict, title: str) -> bool:
    keywords = [k for k in (site.get("keyword_filter") or []) if k]
    if not keywords:
        return True
    lowered = title.lower()
    return any(k.lower() in lowered for k in keywords)


# --------------------------------------------------------------------------
# 실행 모드
# --------------------------------------------------------------------------
def run_check(sites: list[dict], dry_run: bool) -> int:
    state = load_state()
    state_changed = False
    failures = 0

    for site in sites:
        site_id = site.get("id")
        name = site.get("name") or site_id
        if not site_id:
            print("[warn] id 없는 항목을 건너뜁니다", file=sys.stderr)
            continue
        if not site.get("enabled", True):
            print(f"[skip] {site_id} (enabled=false)")
            continue

        try:
            posts = parse_site(site)
        except Exception as exc:  # 한 사이트 실패가 나머지를 막지 않도록
            failures += 1
            print(f"[error] {site_id}: {exc}", file=sys.stderr)
            if not dry_run:
                send_telegram(f"⚠️ <b>{esc(name)}</b>\n확인 실패: {esc(str(exc))}")
            continue

        print(f"[{site_id}] 파싱 {len(posts)}건")
        if dry_run:
            for post in posts[:5]:
                print(f"    - id={post['id']} | date={post.get('date') or '-'} | {post['title']}")
                print(f"      {post.get('url')}")

        # 0건이면 구조 변경 의심 → state 갱신하지 않고 경고
        if not posts:
            failures += 1
            print(f"[warn] {site_id}: 파싱 0건 — 구조 변경 의심, state 유지", file=sys.stderr)
            if not dry_run:
                send_telegram(
                    f"⚠️ <b>{esc(name)}</b>\n글이 0건으로 파싱되었습니다. "
                    f"게시판 구조 변경이 의심되니 선택자를 확인하세요.\n{esc(site.get('url', ''))}"
                )
            continue

        site_state = state["sites"].setdefault(site_id, {"seen": []})
        seen_list = list(site_state.get("seen") or [])
        seen_set = set(seen_list)

        current_ids = [p["id"] for p in posts]

        if not seen_set:
            # 최초 등록: 기준선만 저장하고 알림은 보내지 않는다
            print(f"[{site_id}] 최초 등록 — 기준선 {len(current_ids)}건 저장, 알림 없음")
            if not dry_run:
                site_state["seen"] = current_ids[-SEEN_LIMIT:]
                state_changed = True
            continue

        new_posts = [p for p in posts if p["id"] not in seen_set]
        # 문서 순서는 최신 → 과거. 오래된 것부터 발송한다.
        new_posts.reverse()

        notify = [p for p in new_posts if matches_keywords(site, p["title"])]
        print(f"[{site_id}] 신규 {len(new_posts)}건 / 알림 대상 {len(notify)}건")

        for post in notify:
            message = format_post(name, post)
            if dry_run:
                print("--- 발송 예정 ---")
                print(message)
            else:
                send_telegram(message)

        if not dry_run and new_posts:
            for post in new_posts:
                if post["id"] not in seen_set:
                    seen_set.add(post["id"])
                    seen_list.append(post["id"])
            site_state["seen"] = seen_list[-SEEN_LIMIT:]
            state_changed = True

    if state_changed and not dry_run:
        save_state(state)
        print("state.json 갱신")

    return 1 if failures else 0


def run_preview(sites: list[dict], site_id: str) -> int:
    site = next((s for s in sites if s.get("id") == site_id), None)
    if site is None:
        print(f"[error] 사이트를 찾을 수 없습니다: {site_id}", file=sys.stderr)
        send_telegram(f"⚠️ 파싱 테스트 실패: <code>{esc(site_id)}</code> 설정을 찾을 수 없습니다.")
        return 1

    name = site.get("name") or site_id
    try:
        posts = parse_site(site)
    except Exception as exc:
        print(f"[error] {site_id}: {exc}", file=sys.stderr)
        send_telegram(f"⚠️ <b>{esc(name)}</b> 파싱 테스트 실패\n{esc(str(exc))}")
        return 1

    header = f"🔍 <b>{esc(name)}</b> 파싱 테스트\n총 {len(posts)}건 중 상위 {min(5, len(posts))}건"
    if not posts:
        send_telegram(header + "\n\n결과가 0건입니다. item_selector 를 확인하세요.")
        print(f"[{site_id}] 파싱 0건")
        return 1

    body = []
    for idx, post in enumerate(posts[:5], start=1):
        body.append(
            f"\n\n{idx}. <b>{esc(post['title'])}</b>\n"
            f"날짜: {esc(post.get('date') or '-')}\n"
            f"ID: <code>{esc(post['id'])}</code>\n"
            f"{esc(post.get('url') or '-')}"
        )
    send_telegram(header + "".join(body))
    print(f"[{site_id}] 파싱 {len(posts)}건 — 상위 5건 발송")
    return 0


def run_test() -> int:
    ok = send_telegram("✅ 게시판 알림 봇 연결 확인")
    print("연결 확인 메시지 발송" if ok else "연결 확인 메시지 발송 실패")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="게시판 신규 글 → 텔레그램 알림")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 미발송, stdout 출력, state 미갱신")
    parser.add_argument("--preview", metavar="SITE_ID", help="해당 사이트 파싱 결과 상위 5건 발송 (state 미갱신)")
    parser.add_argument("--test", action="store_true", help="연결 확인 메시지 1건 발송")
    args = parser.parse_args()

    if args.test:
        return run_test()

    sites = load_sites()

    if args.preview:
        return run_preview(sites, args.preview)

    return run_check(sites, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
