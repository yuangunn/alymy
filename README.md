# 게시판 신규 글 → 텔레그램 알림

등록한 게시판을 매일 09:00(KST)에 확인해 신규 글만 텔레그램으로 보냅니다.
감시 대상은 GitHub Pages 프론트엔드에서 추가/수정/삭제할 수 있습니다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `monitor.py` | 체커 본체 |
| `sites.json` | 감시 대상 설정 (프론트엔드가 수정) |
| `state.json` | 사이트별 확인 완료 글 ID (워크플로가 커밋) |
| `.github/workflows/check.yml` | 매일 실행 + 수동 실행 |
| `docs/index.html` | GitHub Pages 설정 화면 |

## 설정

1. **텔레그램 봇**: [@BotFather](https://t.me/BotFather) 로 봇을 만들고 토큰을 받은 뒤,
   봇에게 아무 메시지나 보내고 `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서 chat id 를 확인합니다.
2. **시크릿 등록**: 저장소 Settings → Secrets and variables → Actions 에서
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 를 추가합니다.
3. **Pages 활성화**: Settings → Pages → Source 를 `main` 브랜치의 `/docs` 폴더로 지정합니다.
4. **PAT 발급**: fine-grained PAT 을 이 저장소로 한정해 발급하고
   `Contents: Read and write`, `Actions: Read and write` 권한을 줍니다.
   토큰은 설정 화면의 브라우저 localStorage 에만 저장됩니다.

## 동작

- 사이트를 처음 등록하면 **알림 없이 기준선만 저장**합니다. 과거 글이 한꺼번에 오지 않습니다.
- 신규 글은 오래된 것부터 한 건씩 개별 메시지로 발송합니다.
- 파싱 결과가 0건이면 게시판 구조 변경으로 보고 **state 를 갱신하지 않고** 경고만 보냅니다.
- `keyword_filter` 가 있으면 제목에 그 단어가 포함될 때만 알립니다.
- 사이트별로 오류를 격리해 한 곳이 실패해도 나머지는 계속 확인합니다.

## 로컬 실행

```bash
pip install -r requirements.txt

python monitor.py --dry-run              # 발송/저장 없이 파싱 결과만 출력
python monitor.py --preview gsph-admission  # 상위 5건을 텔레그램으로 발송
python monitor.py --test                 # 연결 확인 메시지 1건
python monitor.py                        # 실제 확인 + 발송 + state 갱신
```

`--preview` 와 `--test` 는 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 환경변수가 필요합니다.

## 파싱 테스트

브라우저에서 대상 사이트를 직접 요청하면 CORS 로 막히므로, 설정 화면의 **파싱 테스트** 버튼은
`check.yml` 워크플로를 `preview_site_id` 입력과 함께 실행합니다. 결과 상위 5건은 텔레그램으로 도착하며
state 는 갱신되지 않습니다.

## sites.json 스키마

| 필드 | 설명 |
| --- | --- |
| `id` | 사이트 식별자. state 의 키로 쓰이므로 바꾸면 기준선이 초기화됩니다 |
| `name` | 알림 메시지 첫 줄에 표시할 이름 |
| `url` | 게시판 목록 페이지 또는 RSS 피드 주소 |
| `enabled` | `false` 면 확인하지 않음 |
| `mode` | `html` \| `rss` |
| `item_selector` | (html) 글 링크를 고르는 CSS 선택자 |
| `title_attr` | (html) 제목이 담긴 속성명. 비우면 링크 텍스트 사용 |
| `title_strip` | (html) 제목에서 지울 문구 목록 |
| `date_regex` | 날짜를 뽑는 정규식 |
| `id_strategy` | `href_param` \| `href_hash` \| `title_hash` \| `rss_guid` |
| `id_param` | `href_param` 일 때 사용할 쿼리 파라미터명 |
| `keyword_filter` | 비우면 전체 알림, 있으면 제목 포함 시에만 알림 |
