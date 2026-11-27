# Notedown Server — FastAPI + Monaco

A self-hosted Notedown sync server with a React document workspace, Monaco Markdown editor, live preview, and file/image uploads. Native FastAPI preserves the existing desktop synchronization API and storage format.

[Notedown desktop app](https://github.com/ImuruKevol/notedown)

---

## 한국어

### 소개

Notedown Server는 FastAPI 기반 동기화 서버와 React 기반 문서 편집·관리 화면을 제공합니다. Monaco에서 Markdown을 작성하고 분할 미리보기로 확인하며, 파일·이미지를 업로드할 수 있습니다. 서버는 파일별 `serverRevision`, 클라이언트가 마지막으로 확인한 `lastKnownRevision`, 콘텐츠 해시를 비교해 충돌을 감지하고, 자동 덮어쓰기가 위험한 경우 충돌 응답을 반환합니다.

### 주요 기능

- React 문서 라이브러리, 워크스페이스 생성, 검색·정렬, 문서 생성·수정·삭제
- 사이드바에서 워크스페이스 이름으로 목록 필터링
- Ctrl/Cmd + P 문서 검색 팔레트: 전체 워크스페이스·문서명 AND 검색, 일치도 정렬, 방향키·Enter로 열기
- 이름·생성일·수정일 토글 버튼과 오름차순/내림차순 전환 버튼, 카드/테이블 보기 및 선택 설정 유지
- 로컬에 번들링한 Monaco Editor와 편집·분할·미리보기 모드
- 문서 위치에 맞춘 편집기 ↔ 미리보기 양방향 스크롤 동기화
- Markdown 도구 모음, 표·체크리스트·코드 강조, 파일 선택·드래그·붙여넣기 업로드
- 전체 첨부 라이브러리: 이미지·파일 검색, 참조 문서 확인, 미사용 파일 전체·선택 정리 및 삭제 전 목록·총용량 확인
- PWA 앱 설치, 홈 화면·Dock·favicon·Apple 아이콘을 기존 책 심볼로 통일
- 저장하지 않은 변경 확인, 브라우저 임시 저장 복원, 리비전 충돌 안내
- 새로 설계한 관리자 화면: 상태·기기·토큰·Git 이력·롤백·계정 설정
- Notedown 메타데이터, 개별 마크다운 파일, 첨부 파일 동기화
- `storage/metadata.db` SQLite 저장소 기반 워크스페이스/노트/첨부 메타데이터 관리
- 동기화 식별자인 `relativePath`와 실제 읽기 쉬운 파일 경로인 `storagePath` 분리
- 파일별 리비전, 콘텐츠 해시, 삭제 tombstone 기반 변경 추적
- 기기 시계가 아닌 서버 Git 콘텐츠 계보를 이용한 양쪽 변경 충돌 판정
- 파일·메타데이터·상태를 함께 보호하는 내구성 트랜잭션과 재시작 복구
- `/api/sync/plan` 기반 업로드/다운로드/삭제 계획 생성
- `/api/sync/file` 기반 단일 파일 업로드
- `/api/sync/attachment` 기반 첨부 파일 업로드
- `/api/files/{relativePath}`, `/api/attachments/{relativePath}` 기반 파일 다운로드
- 충돌 시 어떤 항목도 반영하지 않는 원자적 `/api/sync` 배치 동기화 API
- 최초 실행 `/setup` 화면 또는 환경변수 기반 관리자 계정 설정
- Bearer 토큰 발급, 사용 기록 저장, 관리자 UI에서 토큰 폐기
- `/admin` 관리 화면에서 서버 상태, 클라이언트, 토큰, 파일, 파일 이력 확인
- 관리자 API/UI를 통한 동기화 저장소 초기화
- `storage/files` 내부 Git 저장소를 이용한 파일별 이력 조회 및 롤백
- `/api/openapi.json`, `/api/docs`, `/docs` OpenAPI/Swagger UI 제공
- CORS 허용 Origin 설정

### 실행 환경과 분리 원칙

- Docker Compose v2 권장. 이미지 빌드는 Node.js 22 + Python 3.14를 사용합니다.
- 로컬 실행은 Python 3.14, Git CLI, Node.js 22, npm이 필요합니다.
- 컨테이너는 일반 사용자로 실행되며 Uvicorn worker는 **1개**입니다.
- 개발에 사용한 Mac의 ARM64 Docker에서 네이티브 이미지 빌드와 기동을 확인했습니다.

모든 명령은 `app.py`, `compose.preview.yaml`, `frontend/`가 있는 저장소 루트에서 실행합니다.

| 용도 | 포트 | Docker 프로젝트 | 데이터 |
| --- | --- | --- | --- |
| FastAPI 미리보기 서버 | `5501` | `notedown-preview` | `notedown-preview-data` 볼륨 |
| 브라우저 자동 검증 | `127.0.0.1:5502` | 로컬 프로세스 | 별도 `.verification-data/` |

새 설치의 빈 볼륨은 `/setup`에서 계정을 생성합니다. 미리보기 서버는 전용 데이터 볼륨을 사용합니다.

### Docker로 미리보기 서버 실행

```bash
cp .env.example .env.preview
openssl rand -hex 32
```

출력된 새 랜덤 값을 `.env.preview`의 `NOTE_SYNC_SECRET`에 넣은 다음 실행합니다. 기존 bearer 토큰까지 유지하며 저장소를 이전하는 경우에는 해당 저장소에서 사용한 기존 서명 비밀값을 안전하게 유지해야 합니다.

```bash
docker compose --env-file .env.preview -f compose.preview.yaml up -d --build
docker compose --env-file .env.preview -f compose.preview.yaml ps
curl http://127.0.0.1:5501/api/health
```

브라우저에서 [미리보기 서버](http://localhost:5501)에 접속합니다. 기본 바인딩은 `0.0.0.0:5501`이며 `.env.preview`의 `NOTEDOWN_PREVIEW_BIND_ADDRESS`, `NOTEDOWN_PREVIEW_PORT`로 변경합니다. 미리보기 프로젝트를 관리할 때 같은 `--env-file`과 `-f` 옵션을 사용하세요.

### 로컬 개발 실행

Docker 미리보기 대신 로컬 프로세스로 실행할 때의 명령입니다. 같은 포트를 사용하는 미리보기 컨테이너와 동시에 실행하려면 별도 포트를 지정하세요.

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install pytest
npm --prefix frontend ci
npm --prefix frontend run build
export NOTE_SYNC_SECRET="$(openssl rand -hex 32)"
export NOTE_SYNC_STORAGE=storage
.venv/bin/python -m uvicorn app:create_app --factory --host 127.0.0.1 --port 5501 --workers 1
```

FastAPI가 `frontend/dist`를 직접 제공합니다. Python 모듈을 가져오는 것만으로 서버가 실행되지는 않으며 앱 팩토리 `create_app`을 사용합니다. `python app.py`도 Uvicorn을 실행하지만 기본 포트는 `5500`이므로 기존 서버와 함께 사용할 때는 `PORT`를 명시하세요.

### 문서 검색 팔레트

로그인 후 어느 화면에서든 **Ctrl + P** (Mac: **Cmd + P**) 또는 상단 검색 아이콘으로 문서 검색 팔레트를 엽니다. Monaco 편집기에 포커스가 있어도 사용할 수 있습니다.

- 현재 목록 필터와 관계없이 모든 워크스페이스의 문서명·워크스페이스명을 검색합니다.
- 공백으로 구분한 검색어를 모두 포함해야 하며 대소문자를 구분하지 않습니다. 예: `개발 API`는 워크스페이스명에 `개발`, 문서명에 `API`가 있는 문서도 찾습니다.
- 각 검색어의 등장 횟수를 두 이름에서 합산해 내림차순으로 정렬합니다. 동률이면 문서명 완전 일치, 앞부분 일치, 이름 순으로 정렬합니다. 같은 검색어의 반복 입력은 순위를 바꾸지 않습니다.
- 검색어가 없으면 최근 수정한 문서부터 표시합니다. 위/아래 방향키로 선택하고 Enter로 열며, Esc로 닫습니다. 검색 결과를 클릭해도 열립니다.
- 편집 중 다른 문서를 열면 저장 여부를 확인합니다. 현재 문서를 다시 선택하면 작성 중인 내용을 유지합니다.

검색 로직 검사는 `npm --prefix frontend run test:search`, 격리된 브라우저 검사는 `node scripts/browser-command-palette.mjs`로 실행합니다. 브라우저 검사는 **5502 포트의 테스트 저장소를 초기화**하므로 `.verification-data`를 사용하는 전용 검증 서버에서만 실행하세요. 검증 결과는 `docs/verification/command-palette/`에 기록합니다.

### PWA 앱 설치와 아이콘

로그인 화면이나 로그인 후 상단 우측의 **앱 설치**에서 설치 방법을 확인합니다. Chrome·Edge에서 설치 준비가 되면 **지금 설치**로 브라우저 설치창을 열 수 있습니다. iPhone·iPad는 공유 메뉴의 **홈 화면에 추가**, Mac Safari는 파일 메뉴의 **Dock에 추가**를 사용합니다. 설치한 앱은 별도 창으로 열리며 기존 로그인·문서·관리 기능을 이용합니다.

설치는 HTTPS 주소 또는 같은 기기의 `localhost`/`127.0.0.1`에서 지원됩니다. 다른 기기에서 `http://서버IP:5501`로 접속한다면 HTTPS 주소를 제공해야 합니다. [브라우저 설치 조건](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)을 참고하세요.

문서 열기·저장은 서버 연결이 필요합니다. 서비스 워커는 연결이 끊기면 공개 연결 안내 화면을 표시하며, 문서·첨부·API 응답·로그인 후 HTML은 Cache Storage에 저장하지 않습니다. 기존 편집기의 브라우저 임시 저장 기능은 그대로 유지됩니다. 앱 업데이트 시 편집 화면을 강제로 새로고침하지 않습니다.

아이콘 원본은 `frontend/public/icons/notedown.svg`입니다. 기존 보라색 책 심볼에서 설치용 192/512px PNG, maskable 512px, Apple 180px, favicon SVG/PNG/ICO를 생성합니다. 원본 수정 후 아래 명령으로 다시 생성하고 프런트엔드를 빌드합니다. 기존 Chrome과 Playwright를 사용하며 추가 이미지 라이브러리는 필요하지 않습니다.

```bash
node scripts/generate-icons.mjs --preview /tmp/notedown-icons.png
npm --prefix frontend run build
```

`/manifest.webmanifest`, `/sw.js`, `/offline.html`, `/icons/*`, `/favicon.ico`는 로그인 전에도 제공됩니다. PWA 설정·worker·아이콘은 재검증 헤더로 갱신되며, 서비스 워커의 오프라인 리소스를 변경할 때는 `frontend/public/sw.js`의 캐시 버전도 변경하세요. Vite 개발 서버에서는 서비스 워커를 등록하지 않고 production 빌드에서만 등록합니다.

### 이전 호환성과 편집 범위

기존 `/api/sync*`, 파일·첨부 다운로드, 관리자 API, SQLite 메타데이터, Git 이력, `relativePath` 식별자는 유지됩니다. 저장소와 `NOTE_SYNC_SECRET`을 유지하면 기존 bearer 토큰도 계속 사용할 수 있습니다. 브라우저 세션 쿠키 형식이 바뀌므로 관리자 화면에는 다시 로그인해야 합니다.

문서 제목을 바꿔도 동기화용 문서 ID와 `relativePath`는 유지합니다. 웹 편집의 저장·삭제·첨부는 기존 트랜잭션과 충돌 검사를 거쳐 데스크톱 앱에 동기화됩니다. 데스크톱 클라이언트의 서버 주소가 **새 서버**를 가리켜야 새 서버의 변경을 받습니다.

웹 편집 제한은 UTF-8 문서 **2 MB**, 첨부 파일당 **20 MB**, 편집 API 요청 전체 **25 MB**입니다. 원본 데스크톱 앱의 전용 문서 스타일 지시문과 PDF 내보내기는 구현하지 않았습니다. Markdown·표·체크리스트·코드·첨부 미리보기를 제공하며, 데스크톱 편집기와 모든 기능이 동일하지는 않습니다.

생성일이 없는 기존 문서는 생성일을 임의로 만들지 않고 `정보 없음`으로 표시하며, 생성일 정렬 시 마지막에 배치합니다. 첨부 라이브러리는 모든 저장 문서의 참조를 검사합니다. 파일 정리 직전에 다시 검사하고, 목록 조회 이후 저장소가 변경되었거나 읽을 수 없는 문서가 있으면 삭제하지 않습니다. 다른 편집 창의 저장하지 않은 내용은 검사할 수 없으므로 먼저 문서를 저장하세요.

### 환경변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `NOTE_SYNC_STORAGE` | `storage` | 상태·인증·토큰·문서 저장소. 미리보기 Compose에서는 `/data`로 고정 |
| `NOTE_SYNC_SECRET` | 개발용 기본값 | 세션과 bearer 서명 비밀값. Compose 실행 시 필수이며 `.env.example` 값을 반드시 교체 |
| `NOTE_SYNC_USERNAME` | 없음 | 로컬 실행 시 환경변수 기반 관리자 ID |
| `NOTE_SYNC_PASSWORD` | 없음 | 로컬 실행 시 환경변수 기반 관리자 비밀번호 |
| `NOTE_SYNC_PASSWORD_HASH` | 없음 | 평문 비밀번호 대신 사용할 Werkzeug 해시 |
| `NOTE_SYNC_AUTH_FILE` | 저장소의 `auth.json` | 파일 기반 관리자 인증. 미리보기 Compose에서는 `/data/auth.json` |
| `NOTE_SYNC_CORS_ORIGIN` | `*` | 기존 동기화 API의 CORS 허용 Origin |
| `NOTE_SYNC_PUBLIC_ORIGIN` | 없음 | HTTPS 프록시 뒤에서 브라우저 세션 쓰기에 사용할 공개 Origin. 요청 호스트가 일치할 때 적용 |
| `NOTE_SYNC_SECURE_COOKIES` | `false` | HTTPS 뒤에서 `true`로 설정해 세션 쿠키에 Secure 적용 |
| `NOTEDOWN_PREVIEW_BIND_ADDRESS` | `0.0.0.0` | 미리보기 Compose의 호스트 바인딩 주소 |
| `NOTEDOWN_PREVIEW_PORT` | `5501` | 미리보기 Compose의 호스트 포트 |
| `PORT` | `5500` | `python app.py` 실행 시 사용할 포트 |

환경변수 계정은 `NOTE_SYNC_USERNAME`과 `NOTE_SYNC_PASSWORD` 또는 `NOTE_SYNC_PASSWORD_HASH`를 함께 지정하며, 이 경우 관리자 UI에서 계정 정보를 바꿀 수 없습니다. 기본 미리보기 Compose는 파일 기반 인증을 사용하고 이 계정 환경변수를 컨테이너에 전달하지 않습니다.

HTTPS 프록시에서 `NOTE_SYNC_PUBLIC_ORIGIN`에 브라우저가 접속하는 주소를 지정하면, FastAPI가 프록시와 HTTP로 통신해도 해당 호스트의 브라우저 세션 쓰기 요청을 검증할 수 있습니다. Uvicorn의 전달 헤더 처리가 필요한 경우 `FORWARDED_ALLOW_IPS`에 실제 프록시 IP 또는 필요한 좁은 네트워크 범위만 지정하고 컨테이너 환경변수로 전달하세요. 기본 미리보기 Compose는 `FORWARDED_ALLOW_IPS`를 전달하지 않습니다.

### 저장소 구조

기본 저장 디렉터리는 `storage/`입니다.

- `storage/state.json`: 서버 리비전, 파일/첨부 manifest, 클라이언트 상태
- `storage/metadata.db`: 서버가 보유한 Notedown 워크스페이스/노트/첨부 메타데이터 SQLite 저장소
- `storage/files/`: 동기화된 마크다운 파일, 첨부 파일, 내부 Git 저장소. 실제 파일은 워크스페이스 이름, 노트 제목, 첨부 파일명을 기반으로 읽기 쉬운 `storagePath`에 저장됩니다.
- `storage/auth.json`: `/setup`에서 만든 파일 기반 관리자 계정
- `storage/tokens.json`: 발급된 앱 토큰과 최근 사용 정보
- `storage/.sync-transaction/`: 동기화 변경 중에만 존재하는 복구 체크포인트. 중단 후 서버 시작 시 자동 롤백됩니다.

`metadata.json`은 더 이상 생성하지 않습니다. `storage/files`는 서버 시작 시 Git 저장소로 초기화됩니다. 파일 저장, 삭제, 롤백은 Git 커밋으로 기록됩니다.

### 관리 화면

- `/setup`: 최초 관리자 계정 생성
- `/login`: 관리자 로그인
- `/admin`, `/editor`: 문서 라이브러리·Monaco 편집기·미리보기, 사이드바의 서버 관리 화면
- `/logout`: 관리자 세션 종료

### 편집 API

다음 경로는 관리자 세션 또는 기존 bearer 인증을 사용합니다. 문서 저장에는 읽었을 때 받은 `revision`을 보내야 하며 오래된 리비전은 `409 revision_conflict`를 반환합니다.

| 메서드 | 경로 | 기능 |
| --- | --- | --- |
| `GET` | `/api/editor/library` | 워크스페이스·문서·편집 제한 조회 |
| `POST` | `/api/editor/workspaces` | 워크스페이스 생성: `{ "name": "프로젝트" }` |
| `POST` | `/api/editor/notes` | 문서 생성: `workspaceId`, `title`, `content` |
| `GET` | `/api/editor/notes/{note_id}` | UTF-8 내용·메타데이터·리비전 조회 |
| `PUT` | `/api/editor/notes/{note_id}` | `title`, `content`, `revision`으로 저장 |
| `DELETE` | `/api/editor/notes/{note_id}?revision=…` | 문서와 연결 첨부 삭제 |
| `POST` | `/api/editor/notes/{note_id}/attachments` | multipart `file` 필드로 첨부 업로드 |
| `DELETE` | `/api/editor/notes/{note_id}/attachments/{attachment_id}?revision=…` | 해당 첨부 리비전을 확인한 후 삭제 |
| `GET` | `/api/editor/attachments/{attachment_id}` | 인증된 원본 첨부 응답; `?download=true`로 다운로드 |
| `GET` | `/api/editor/attachment-library` | 전체 첨부·참조 문서·정리 가능 여부·저장소 리비전 조회 |
| `POST` | `/api/editor/attachments/cleanup` | `serverRevision`, `attachmentIds`로 미사용 파일 선택 정리; 변경 시 `409`, 배치 전체 적용 또는 복구 |

업로드 응답에는 데스크톱과 호환되는 Markdown 링크 및 미리보기 URL이 포함됩니다. 문서 저장 시 링크를 내용에 포함해야 합니다. 기존 `/api/attachments/{relativePath}`는 계속 base64 JSON 응답을 반환합니다. 웹 편집기 원본 첨부 경로와 구분하세요.

### 기존 동기화·관리 API 개요

인증이 필요 없는 API:

- `GET /api/health`: 서버 상태 확인
- `GET /api/openapi.json`, `GET /openapi.json`: OpenAPI 3.1 문서
- `GET /api/docs`, `GET /docs`: Swagger UI
- `GET /api/setup/status`: 초기 관리자 계정 설정 여부 확인
- `POST /api/setup`: 최초 관리자 계정 생성
- `POST /api/login`: 관리자 인증 및 Bearer 토큰 발급
- `POST /api/logout`: 관리자 세션 종료

Bearer 토큰 또는 관리자 세션이 필요한 API:

- `GET /api/manifest`: 서버 리비전, 파일 manifest, 클라이언트 상태 조회
- `GET /api/files/{relativePath}`: 서버 파일 다운로드
- `GET /api/attachments/{relativePath}`: 첨부 파일 다운로드
- `POST /api/sync/plan`: 동기화 계획 생성
- `POST /api/sync/file`: 단일 파일 업로드 또는 삭제 반영
- `POST /api/sync/attachment`: 단일 첨부 파일 업로드 또는 삭제 반영
- `POST /api/sync`: 배치 동기화
- `GET /api/admin/account`: 관리자 계정 정보 조회
- `POST /api/admin/account`: 파일 기반 관리자 계정 수정
- `GET /api/admin/tokens`: 발급 토큰 목록 조회
- `DELETE /api/admin/tokens/{tokenId}`: 토큰 폐기
- `GET /api/admin/files/{relativePath}/history`: 파일 Git 이력 조회
- `GET /api/admin/files/{relativePath}/history/{commit}`: 특정 커밋의 파일 내용 조회
- `POST /api/admin/files/{relativePath}/rollback`: 파일을 특정 커밋 상태로 롤백
- `POST /api/admin/storage/reset`: 동기화 파일, 파일 이력, 서버 메타데이터를 초기화

인증 토큰은 로그인 응답의 `accessToken` 값을 사용합니다.

```http
Authorization: Bearer <accessToken>
```

### 권장 동기화 흐름

1. 클라이언트가 `/api/login`으로 로그인하고 `accessToken`을 저장합니다.
2. 전체 동기화가 필요하면 `/api/sync/plan`에 로컬 메타데이터, `knownFiles`, `knownAttachments`를 보냅니다.
   새 기기처럼 서버 파일을 아직 내려받지 않은 상태에서는 `knownFiles`를 비우거나
   `deleted`를 생략하세요. 서버는 metadata에 없다는 이유만으로 서버 파일을 삭제하지 않습니다.
3. 응답의 `plan.uploadFiles`는 `/api/sync/file`로 업로드합니다.
4. 응답의 `plan.uploadAttachments`는 `/api/sync/attachment`로 업로드합니다.
5. 응답의 `plan.downloadFiles`는 `/api/files/{relativePath}`로, `plan.downloadAttachments`는 `/api/attachments/{relativePath}`로 다운로드합니다.
6. 응답의 `plan.deleteServerFiles`/`plan.deleteServerAttachments`는 서버 삭제로, `plan.deleteLocalFiles`/`plan.deleteLocalAttachments`는 로컬 삭제로 반영합니다.
   서버 삭제를 계획으로 요청하려면 해당 `knownFiles` 항목에 `deleted: true`와
   `lastKnownRevision`을 명시하거나 `/api/sync/file`에 `deleted: true`를 보내세요.
7. 각 처리 후 응답의 `manifest.serverRevision`과 파일/첨부별 `revision`을 로컬 동기화 상태에 저장합니다.
8. `plan.conflicts`가 있으면 자동 덮어쓰기 대신 사용자 선택 또는 병합 UI를 거칩니다.
9. 파일 저장 이벤트가 발생하면 전체 동기화를 기다리지 않고 `/api/sync/file` 또는 `/api/sync/attachment`로 변경 항목만 업로드할 수 있습니다.

레거시 `/api/sync` 배치 요청은 메타데이터·파일·첨부 중 하나라도 충돌하면
아무 항목도 저장하지 않습니다. 이 경우 `accepted`/`acceptedAttachments`는
비어 있고, 쓰지 않은 사전 판정 결과는 `skipped`/`skippedAttachments`에
반환됩니다. 한 요청 안에서 같은 `relativePath`를 중복 전송하면 쓰기 전에
400 오류로 거부됩니다.

### 예시 요청

로그인:

```json
{
  "username": "admin",
  "password": "change-this-password"
}
```

동기화 계획:

```json
{
  "clientId": "macbook-1",
  "baseRevision": 0,
  "clientInfo": {
    "hostname": "macbook-pro",
    "platform": "macOS",
    "appVersion": "1.2.3"
  },
  "metadata": {
    "lastKnownRevision": 0,
    "body": {
      "version": 1,
      "workspaces": [],
      "notes": []
    }
  },
  "knownFiles": []
}
```

단일 파일 업로드:

```json
{
  "clientId": "macbook-1",
  "relativePath": "memo/note.md",
  "lastKnownRevision": 0,
  "updatedAtMs": 1780030055707,
  "contentEncoding": "utf-8",
  "content": "# 새 노트\n",
  "workspace": {
    "id": "memo",
    "name": "memo"
  },
  "note": {
    "id": "note-1",
    "title": "새 노트",
    "workspace": "memo",
    "relativePath": "memo/note.md",
    "storagePath": "memo/새 노트.md",
    "updatedAtMs": 1780030055707
  }
}
```

첨부 파일 업로드:

```json
{
  "clientId": "macbook-1",
  "relativePath": "memo/.attachments/note-1/diagram.png",
  "lastKnownRevision": 0,
  "updatedAtMs": 1780030055707,
  "contentEncoding": "base64",
  "content": "iVBORw0KGgo=",
  "attachment": {
    "relativePath": "memo/.attachments/note-1/diagram.png",
    "storagePath": "memo/attachments/새 노트/diagram.png",
    "fileName": "diagram.png",
    "mimeType": "image/png",
    "noteRelativePath": "memo/note.md"
  }
}
```

파일 삭제:

```json
{
  "clientId": "macbook-1",
  "relativePath": "memo/old.md",
  "lastKnownRevision": 3,
  "deleted": true,
  "updatedAtMs": 1780030055707
}
```

계획에서 서버 삭제를 명시하는 `knownFiles` tombstone:

```json
{
  "relativePath": "memo/old.md",
  "lastKnownRevision": 3,
  "deleted": true
}
```

### 검증

API·저장소·동기화·편집 회귀 테스트:

```bash
.venv/bin/python -m pytest -q
```

프런트엔드를 수정했다면 `npm --prefix frontend run build`를 먼저 실행합니다. 브라우저 검증은 로컬 Google Chrome을 설치한 상태에서 **전용 저장소와 포트**를 사용합니다. 아래 서버를 별도 터미널에서 실행하세요.

```bash
env -u NOTE_SYNC_USERNAME -u NOTE_SYNC_PASSWORD -u NOTE_SYNC_PASSWORD_HASH -u NOTE_SYNC_AUTH_FILE \
  NOTE_SYNC_STORAGE=.verification-data NOTE_SYNC_SECURE_COOKIES=false NOTE_SYNC_SECRET="$(openssl rand -hex 32)" \
  .venv/bin/python -m uvicorn app:create_app --factory --host 127.0.0.1 --port 5502 --workers 1
```

다른 터미널에서:

```bash
node scripts/browser-check.mjs
node scripts/browser-sidebar.mjs
node scripts/browser-enhancements.mjs
node scripts/browser-pwa.mjs
node --test frontend/src/*.test.js
```

스크립트는 `http://127.0.0.1:5502`에만 접속하며 **그 서버의 저장소를 초기화**한 뒤 테스트 문서를 만듭니다. 이 포트에 운영·미리보기 저장소를 연결하면 안 됩니다. 로그인·편집·첨부·충돌·이동 확인·관리·모바일 흐름을 검증하고 `docs/verification/`에 스크린샷과 `browser-results.json`을 기록합니다. 운영 `5500`이나 미리보기 `5501`을 대상으로 실행하지 않습니다.

사이드바 검사는 워크스페이스 목록 필터와 모바일 메뉴를 확인합니다. 추가 기능 스크립트는 정렬·보기 전환, 분할 스크롤, 첨부 검색·정리·삭제 직전 참조 추가 충돌, 모바일 배치를 검증하고 `docs/verification/enhancements/`에 결과를 기록합니다.

PWA 검사는 앞선 스크립트가 만든 테스트 계정을 사용하며 저장소를 초기화하지 않습니다. 설치 가능성·아이콘·service worker·캐시·연결 복구 및 데스크톱/모바일 화면을 검사하고 `docs/verification/pwa/`에 결과를 기록합니다. 브라우저 스크립트는 모두 순차 실행합니다.

### 보안 메모

- 운영 환경에서는 `NOTE_SYNC_SECRET`을 충분히 긴 랜덤 값으로 설정하세요.
- 가능하면 HTTPS 뒤에서 실행하고, 리버스 프록시에서 접근 제어를 적용하세요.
- `NOTE_SYNC_CORS_ORIGIN=*`은 개발에는 편리하지만 운영에서는 신뢰할 수 있는 Origin으로 제한하는 것이 좋습니다.
- `storage/`에는 인증 정보, 토큰, 문서 파일이 포함되므로 백업과 권한 관리를 신중히 설정하세요.

### 라이선스

MIT License. 자세한 내용은 [LICENSE](LICENSE)를 확인하세요.

---

## English

### Overview

Notedown Server combines a native FastAPI sync service with a React workspace and bundled Monaco Markdown editor, live preview, and attachment uploads. It compares each file's `serverRevision`, the client's `lastKnownRevision`, and content hashes to detect conflicts before overwriting remote or local changes.

### Features

- React document library, workspaces, search/sort, and create/edit/delete workflows
- Workspace name filtering in the sidebar
- Bundled Monaco with source, split, and preview modes; Markdown toolbar, tables, tasks, and code highlighting
- Persistent card/table views; ascending/descending name, creation-date, and modification-date sorting
- Bidirectional source/preview scrolling mapped to Markdown block positions
- Global attachment library with reference tracking, all-unused or selected-file cleanup, and a confirmation list with total storage
- Installable PWA with a shared book symbol for app, maskable, Apple and browser icons
- File/image uploads, drag-and-drop and paste; unsaved changes, local draft recovery, and revision conflict handling
- Redesigned administration for status, devices, access tokens, Git history/rollback, and settings
- Synchronizes Notedown metadata, markdown files, and attachments
- Stores workspace, note, and attachment metadata in `storage/metadata.db` SQLite tables
- Separates `relativePath` as the sync identity from readable physical `storagePath` values
- Tracks per-file revisions, content hashes, and deletion tombstones
- Detects two-sided changes from Git-backed content ancestry instead of device clocks
- Protects files, metadata, and state with durable transactions and restart recovery
- Builds upload, download, and delete plans through `/api/sync/plan`
- Accepts single-file uploads and deletes through `/api/sync/file`
- Accepts attachment uploads and deletes through `/api/sync/attachment`
- Downloads files through `/api/files/{relativePath}` and attachments through `/api/attachments/{relativePath}`
- Provides an atomic `/api/sync` batch endpoint that applies nothing on conflict
- Supports first-run `/setup` credentials or environment-managed admin credentials
- Issues Bearer tokens and lets admins revoke tokens from the web UI
- Provides an `/admin` UI for server status, clients, tokens, files, history, rollback, and storage reset
- Stores synced files in an internal Git repository under `storage/files`
- Exposes OpenAPI JSON and Swagger UI at `/api/openapi.json`, `/api/docs`, and `/docs`
- Provides configurable CORS origin support

### Run alongside the existing service

Use Docker Compose v2, or Python 3.14, Git, Node.js 22, and npm for local development. Run commands from the repository root. The image uses a Node.js 22 frontend build stage, Python 3.14, a non-root user, and a single Uvicorn worker.

```bash
cp .env.example .env.preview
openssl rand -hex 32
```

Replace `NOTE_SYNC_SECRET` in `.env.preview` with the generated value, then:

```bash
docker compose --env-file .env.preview -f compose.preview.yaml up -d --build
curl http://127.0.0.1:5501/api/health
```

Open [the preview workspace](http://localhost:5501). This uses project `notedown-preview`, volume `notedown-preview-data`, and host port `5501` (binding `0.0.0.0` by default). The existing service on `5500` and its volume remain independent. Writes to one server do not appear on the other. A new empty volume opens `/setup`; the verified preview uses a copied snapshot with the existing file-backed account. No credentials are included in this repository's documentation.

Keep the same `NOTE_SYNC_SECRET`, auth file, and tokens file when migrating a repository whose existing bearer tokens must remain valid. Browser sessions require a fresh login after the migration. Storage formats, logical IDs, synchronization APIs, and Git history remain compatible. Point desktop clients at the new server to synchronize with that server.

For local development:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install pytest
npm --prefix frontend ci
npm --prefix frontend run build
export NOTE_SYNC_SECRET="$(openssl rand -hex 32)"
export NOTE_SYNC_STORAGE=storage
.venv/bin/python -m uvicorn app:create_app --factory --host 127.0.0.1 --port 5501 --workers 1
```

FastAPI serves `frontend/dist`. Keep a single worker. `python app.py` also runs Uvicorn and accepts `PORT`; its default is `5500`, which must not collide with the existing service.

### Configuration and editor API

See [.env.example](.env.example) and the Korean environment table above. `NOTE_SYNC_SECRET` is required by Compose; `NOTE_SYNC_STORAGE` defaults to `storage` locally and is `/data` in preview Compose. `NOTE_SYNC_SECURE_COOKIES=true` enables Secure session cookies behind HTTPS. Set `NOTEDOWN_PREVIEW_PORT` and `NOTEDOWN_PREVIEW_BIND_ADDRESS` to change the preview host binding.

For HTTPS reverse proxies, set `NOTE_SYNC_PUBLIC_ORIGIN` to the browser-facing origin. It applies to browser session writes for the matching host when the proxy connects to FastAPI over HTTP. When Uvicorn needs to trust forwarding headers, pass `FORWARDED_ALLOW_IPS` with the trusted proxy IP or narrow network range. Preview Compose does not forward that variable by default. Optional username/password environment authentication is supported for local runs; preview Compose uses file-backed authentication unless customized.

The authenticated `/api/editor` routes provide library listing, workspace/document creation, revision-checked document save/deletion, multipart uploads, attachment deletion, and raw attachment responses. The route table above documents each path, and `/api/docs` includes the editor schemas alongside the existing sync API. Legacy attachment downloads continue to use base64 JSON.

Limits are **2 MB** per UTF-8 note, **20 MB** per attachment, and **25 MB** per editor request. Desktop-specific document style directives and PDF export are outside this implementation; the web editor does not claim complete desktop feature parity.

### Storage Layout

The default storage directory is `storage/`.

- `storage/state.json`: server revision, file/attachment manifest, and client state
- `storage/metadata.db`: server-side Notedown workspace, note, and attachment metadata SQLite store
- `storage/files/`: synchronized markdown files, attachments, and the internal Git repository. Actual files use readable `storagePath` values based on workspace names, note titles, and attachment names.
- `storage/auth.json`: file-backed admin credentials created by `/setup`
- `storage/tokens.json`: issued app tokens and last-used metadata
- `storage/.sync-transaction/`: temporary recovery checkpoint used during a mutation and rolled back automatically after an interrupted restart

`metadata.json` is no longer generated. `storage/files` is initialized as a Git repository when the server starts. File saves, deletes, and rollbacks are recorded as commits.

### Admin UI

- `/setup`: create the first admin account
- `/login`: admin login
- `/admin`, `/editor`: document library and Monaco editor; server administration is available from the sidebar
- `/logout`: clear the admin session

### API Overview

Public endpoints:

- `GET /api/health`: health check
- `GET /api/openapi.json`, `GET /openapi.json`: OpenAPI 3.1 document
- `GET /api/docs`, `GET /docs`: Swagger UI
- `GET /api/setup/status`: setup status
- `POST /api/setup`: create the first admin account
- `POST /api/login`: authenticate and issue a Bearer token
- `POST /api/logout`: clear the admin session

Authenticated endpoints:

- `GET /api/manifest`: server revision, file manifest, and client status
- `GET /api/files/{relativePath}`: download a server file
- `GET /api/attachments/{relativePath}`: download an attachment
- `POST /api/sync/plan`: create a sync plan
- `POST /api/sync/file`: upload or delete a single file
- `POST /api/sync/attachment`: upload or delete a single attachment
- `POST /api/sync`: batch sync
- `GET /api/admin/account`: read admin account metadata
- `POST /api/admin/account`: update file-backed admin credentials
- `GET /api/admin/tokens`: list issued tokens
- `DELETE /api/admin/tokens/{tokenId}`: revoke a token
- `GET /api/admin/files/{relativePath}/history`: list Git history for a file
- `GET /api/admin/files/{relativePath}/history/{commit}`: read a file at a specific commit
- `POST /api/admin/files/{relativePath}/rollback`: roll back a file to a commit
- `POST /api/admin/storage/reset`: reset synced files, file history, and server metadata

Use the `accessToken` from `/api/login` as a Bearer token.

```http
Authorization: Bearer <accessToken>
```

### Recommended Sync Flow

1. Log in with `/api/login` and store the `accessToken`.
2. For a full sync, send local metadata, `knownFiles`, and `knownAttachments` to `/api/sync/plan`.
   On a new device that has not downloaded server files yet, leave `knownFiles`
   empty or omit `deleted`; missing client metadata is not treated as a server
   delete.
3. Upload `plan.uploadFiles` with `/api/sync/file`.
4. Upload `plan.uploadAttachments` with `/api/sync/attachment`.
5. Download `plan.downloadFiles` with `/api/files/{relativePath}` and `plan.downloadAttachments` with `/api/attachments/{relativePath}`.
6. Apply `plan.deleteServerFiles`/`plan.deleteServerAttachments` on the server side and `plan.deleteLocalFiles`/`plan.deleteLocalAttachments` locally.
   To request a server delete from planning, include the path in `knownFiles`
   with `deleted: true` and `lastKnownRevision`, or call `/api/sync/file` with
   `deleted: true`.
7. Store `manifest.serverRevision` and each file/attachment `revision` in the client's sync state.
8. If `plan.conflicts` is not empty, ask the user to choose or merge instead of overwriting automatically.
9. On file-save events, upload only the changed item with `/api/sync/file` or `/api/sync/attachment`.

The legacy `/api/sync` batch endpoint does not persist any metadata, file, or
attachment when one item conflicts. In that response, `accepted` and
`acceptedAttachments` are empty, while preflight results that were not written
are returned in `skipped` and `skippedAttachments`. Duplicate `relativePath`
entries in one request are rejected with HTTP 400 before any write.

### Example Requests

Login:

```json
{
  "username": "admin",
  "password": "change-this-password"
}
```

Sync plan:

```json
{
  "clientId": "macbook-1",
  "baseRevision": 0,
  "clientInfo": {
    "hostname": "macbook-pro",
    "platform": "macOS",
    "appVersion": "1.2.3"
  },
  "metadata": {
    "lastKnownRevision": 0,
    "body": {
      "version": 1,
      "workspaces": [],
      "notes": []
    }
  },
  "knownFiles": []
}
```

Single-file upload:

```json
{
  "clientId": "macbook-1",
  "relativePath": "memo/note.md",
  "lastKnownRevision": 0,
  "updatedAtMs": 1780030055707,
  "contentEncoding": "utf-8",
  "content": "# New note\n",
  "workspace": {
    "id": "memo",
    "name": "memo"
  },
  "note": {
    "id": "note-1",
    "title": "New note",
    "workspace": "memo",
    "relativePath": "memo/note.md",
    "storagePath": "memo/New note.md",
    "updatedAtMs": 1780030055707
  }
}
```

Attachment upload:

```json
{
  "clientId": "macbook-1",
  "relativePath": "memo/.attachments/note-1/diagram.png",
  "lastKnownRevision": 0,
  "updatedAtMs": 1780030055707,
  "contentEncoding": "base64",
  "content": "iVBORw0KGgo=",
  "attachment": {
    "relativePath": "memo/.attachments/note-1/diagram.png",
    "storagePath": "memo/attachments/New note/diagram.png",
    "fileName": "diagram.png",
    "mimeType": "image/png",
    "noteRelativePath": "memo/note.md"
  }
}
```

File delete:

```json
{
  "clientId": "macbook-1",
  "relativePath": "memo/old.md",
  "lastKnownRevision": 3,
  "deleted": true,
  "updatedAtMs": 1780030055707
}
```

`knownFiles` tombstone for planned server delete:

```json
{
  "relativePath": "memo/old.md",
  "lastKnownRevision": 3,
  "deleted": true
}
```

### Tests and browser screenshots

```bash
.venv/bin/python -m pytest -q
```

Rebuild the frontend after changes. The browser script requires a local Google Chrome installation and the isolated verification server described in the Korean verification section above: host `127.0.0.1`, port `5502`, storage `.verification-data`.

```bash
node scripts/browser-check.mjs
node scripts/browser-sidebar.mjs
node scripts/browser-enhancements.mjs
node scripts/browser-pwa.mjs
node --test frontend/src/*.test.js
```

The script **resets that verification server's storage** before creating its test data. Never connect that server to the production or preview volume. Screenshots and `browser-results.json` are written to `docs/verification/`.

### Security Notes

- Set `NOTE_SYNC_SECRET` to a long random value in production.
- Run the service behind HTTPS and apply access control at the reverse proxy when possible.
- `NOTE_SYNC_CORS_ORIGIN=*` is convenient for development, but production deployments should restrict it to trusted origins.
- `storage/` contains credentials, tokens, and note files. Configure backups and filesystem permissions carefully.

### License

MIT License. See [LICENSE](LICENSE) for details.
