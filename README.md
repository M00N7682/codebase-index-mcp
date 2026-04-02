# codebase-index-mcp

AI 코딩 도구(Claude Code 등)가 코드를 찾느라 낭비하는 시간과 비용을 없애주는 도구입니다.

## 이게 왜 필요한가요?

AI에게 "이 버그 고쳐줘"라고 시키면, AI는 바로 고치지 않습니다.
먼저 **프로젝트 구조를 파악하느라** 파일을 하나하나 뒤집니다.

```
사람: "로그인 버그 고쳐줘"

AI:  (파일 목록 검색...)
     (이 파일 열어볼게...)
     (아 이건 아니네, 저 파일도 열어볼게...)
     (이 파일이 뭘 import하는지 볼게...)
     (이것도 읽어볼게...)
     ...8~15번 반복...
     "아, 여기였군요! 이제 고칠게요."
```

**이 탐색 과정이 전체 비용의 30~50%를 차지합니다.**
그리고 새로운 대화를 시작할 때마다 처음부터 다시 합니다.

## 이 도구가 하는 일

프로젝트의 **지도를 미리 만들어둡니다.**

AI가 파일을 하나하나 뒤질 필요 없이, 지도를 보고 바로 정확한 파일을 찾습니다.

```
사람: "로그인 버그 고쳐줘"

AI:  (지도 한 번 조회 → 관련 파일 3개 즉시 반환)
     "이 파일이 문제입니다. 고칠게요."
```

| | 기존 | 이 도구 사용 |
|---|---|---|
| 탐색 횟수 | 8~15번 | **1번** |
| 탐색 시간 | 30~60초 | **2ms** |
| 탐색 비용 | 5,000~15,000 토큰 | **~500 토큰** |

## 어떻게 동작하나요?

1. **코드를 분석합니다** — 모든 파일에서 함수, 클래스, import 관계를 자동으로 추출합니다
2. **지도를 저장합니다** — 분석 결과를 로컬 데이터베이스에 저장합니다 (한 번만 하면 됨)
3. **변경분만 업데이트합니다** — git commit이 생기면 바뀐 파일만 다시 분석합니다
4. **질문하면 답합니다** — "이 작업에 필요한 파일이 뭐야?"라고 물으면 즉시 알려줍니다

비유하자면:
- **기존**: 매일 출근할 때마다 건물 전체를 돌아다니며 회의실 위치를 파악하는 것
- **이 도구**: 건물 안내도를 한 번 만들어두고, 바뀔 때만 업데이트하는 것

## 제공하는 기능 (5가지)

| 기능 | 하는 일 | 예시 |
|------|---------|------|
| **find_files_for_task** | 작업 설명 → 관련 파일 찾기 | "결제 로직 수정" → `payment_service.py`, `checkout.py` |
| **get_project_overview** | 프로젝트 전체 구조 한눈에 보기 | 파일 수, 언어 비율, 디렉토리 구성 |
| **get_file_context** | 파일 내용 안 읽고 요약만 보기 | 어떤 함수/클래스가 있는지, 뭘 import하는지 |
| **get_recent_changes** | 최근 뭐가 바뀌었는지 보기 | 최근 커밋, 변경된 파일 목록 |
| **rebuild_index** | 지도 강제 재구축 | 인덱스가 이상할 때 리셋 |

한국어로 검색해도 됩니다:

```
"네이버 카탈로그 크롤링 에러"  →  naver/catalog_scraper.py 찾아줌
"몽고 연결 타임아웃"          →  mongodb/mongo_config.py 찾아줌
```

## 기술 스펙

관심 있는 분들을 위한 상세 내용입니다.

### 지원 언어

Python, TypeScript, JavaScript, Go, Rust, Java, Kotlin, Ruby, C, C++, C#, Swift, PHP (tree-sitter AST 파싱)

### 검색 랭킹 방식

단순 키워드 매칭이 아닙니다. 세 가지 신호를 조합합니다:

1. **경로 매칭** — 파일명과 디렉토리명에 검색어가 포함되는지 (가장 강한 신호)
2. **심볼 매칭** — 함수명, 클래스명에 검색어가 포함되는지
3. **PageRank** — 다른 파일들이 많이 import하는 핵심 파일에 가산점

### 성능 (569개 파일, 20만 줄 프로젝트 기준)

| 항목 | 수치 |
|------|------|
| 최초 인덱싱 | 1.1초 |
| 이후 업데이트 | 0.1초 미만 |
| 검색 속도 | 1~2ms |
| DB 크기 | ~2MB |

### 아키텍처

```
src/codebase_index/
├── server.py              # MCP 서버 (AI 도구와 연결되는 인터페이스)
├── models.py              # 데이터 구조 정의
├── git_ops.py             # git 연동
├── treesitter_parser.py   # 코드 분석 (AST 파싱)
├── regex_parser.py        # 코드 분석 (정규식, 폴백)
├── parser.py              # 분석기 선택 (tree-sitter 우선)
├── storage.py             # 데이터베이스 (SQLite + 전문검색)
├── indexer.py             # 인덱스 구축/업데이트 + PageRank
└── ranking.py             # 검색 + 순위 매기기
```

## 설치

### 요구사항

- Python 3.11+
- git

### 설치 방법

```bash
git clone https://github.com/M00N7682/codebase-index-mcp.git
cd codebase-index-mcp
uv venv && uv pip install -e .
```

### Claude Code에 연결하기

`~/.mcp.json`에 추가:

```json
{
  "mcpServers": {
    "codebase-index": {
      "type": "stdio",
      "command": "/path/to/codebase-index-mcp/.venv/bin/python",
      "args": ["-m", "codebase_index"]
    }
  }
}
```

`~/.claude/settings.json`에 활성화:

```json
{
  "enabledMcpjsonServers": ["codebase-index"]
}
```

Claude Code 재시작하면 바로 사용 가능합니다.

## 테스트

```bash
.venv/bin/python tests/test_all.py
```

## License

MIT
