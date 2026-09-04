# 사용 가이드 · selfheal

"어떻게 돌아가는가"는 [SERVICE.md](SERVICE.md), 여기는 **지금 당장 어떻게 쓰는가**만.

> `selfheal.zip`(또는 압축을 푼 폴더)과 함께 이 문서를 받으셨다면, 폴더 이름과 무관하게
> 지금 이 문서가 있는 폴더에서 아래를 그대로 따라 하시면 됩니다.

## 1. 준비

```bash
pip install -r requirements.txt
cp .env.example .env   # 열어서 AWS_ACCESS_KEY_ID·AWS_SECRET_ACCESS_KEY·AWS_DEFAULT_REGION 채우기
```

Bedrock **Claude Sonnet 4.5 + Titan 임베딩**에 접근 가능한 AWS 계정이 필요합니다(고정
스택, `AGENTS.md`). 이게 없으면 아무것도 동작하지 않으니 가장 먼저 확인하십시오.
(`ragas` 설치는 환경에 따라 실패할 수 있지만 에이전트 실행과는 무관합니다.)

**(선택) LangSmith 추적**은 `.env` 의 `LANGSMITH_TRACING=true` 와 `LANGSMITH_API_KEY`
를 **둘 다** 채워야 켜집니다. 켜면 소스 코드가 포함된 프롬프트가 외부로 나가니, 필요
없으면 그냥 두십시오(기본 꺼짐).

## 2. 기본 사용법

```bash
./run.sh ./내프로젝트경로            # 제안만 — 원본 불변, diff 를 보여줌
./run.sh ./내프로젝트경로 --apply    # 검수 후 실제 적용
```

되돌리려면 git 을 쓰십시오(`git checkout -- <파일>`) — 자체 되돌리기 기능은 없습니다.
폴더 대신 파일 하나를 줘도 됩니다(테스트는 프로젝트 전체를, 수정은 그 파일만).

| exit | 뜻 | 할 일 |
|---|---|---|
| 0 | 고쳐서 적용됨 | — |
| 1 | 못 고침 | `.heal/report.json` 의 `attempt_log` 확인 |
| 2 | 고칠 게 없음 | — (정상) |
| 3 | 전제 실패 (테스트·툴체인 없음) | 화면 사유 확인 |
| 4 | 제안 준비됨, 승인 대기 (기본) | diff 검수 후 `--apply` |
| 5 | 모델 사용 불가 (한도·인증·네트워크) | 잠시 뒤 재시도 |

## 3. 자주 쓰는 플래그

| 플래그 | 용도 |
|---|---|
| `--max-attempts N` | 재시도 상한 (기본 3) |
| `--lang {python,javascript,go}` | 언어 자동 감지 override |
| `--test-cmd "..."` | 테스트 명령 직접 지정 |
| `--cross-language` | 다른 언어 패턴까지 참고 (기본은 언어별 격리) |
| `--merge-threshold N` | 메모리 병합 민감도 (기본 0.55) |
| `--model sonnet\|haiku` | 토큰 한도 시 가벼운 모델로 전환 |
| `--trace` / `--no-trace` | 이번 실행만 LangSmith 켜기/끄기 |
| `--stats` | 누적 버그 패턴을 발생 횟수순으로 |

전체 목록: `./run.sh --help`

## 4. 메모리(학습) 효과

같은 계열·다른 형태 버그를 순서대로 돌리면 과거 사례가 다음 수정에 주입됩니다.

```bash
./run.sh ./data/samples/py-index   # 1회차 — 패턴 축적 (주입 0건, 정상)
./run.sh ./data/samples/py-dict    # 2회차 — 같은 계열 다른 버그 (주입 발생)
```

`.heal/report.json` 의 `memory_hits[].injected` 가 `true` 면 실제로 주입된 것입니다.

## 5. 문제 해결

- **같은 버그가 매번 새 패턴으로 저장됨** → `--merge-threshold` 를 올리기
- **exit 5 가 계속 뜸** → `.env` 자격증명 확인, 안 되면 `--model haiku` 시도
- **소스가 프롬프트로 나가는 게 불안함** → 모델에는 항상 원본이 감(마스킹하면 파일 재생성
  때 코드가 망가질 수 있음). `trace.log`·화면·LangSmith 사본에서는 비밀값이 자동으로 지워짐

## 6. 내가 개발 중인 다른 프로젝트에 적용하기

설치가 따로 필요 없습니다. 이 폴더 안에서 대상 경로만 바꿔 가리키면 됩니다.

```bash
./run.sh /path/to/내프로젝트                  # 그냥 실행
./install-hooks.sh /path/to/내프로젝트        # 커밋·푸시마다 자동 실행 (git 훅 설치)
```

대상은 **Python/JavaScript/Go 마커 파일**과 **테스트**가 있어야 합니다(다른 언어는
`src/tools.py` 의 `ADAPTERS` 에 항목 추가, [README.md](README.md) 8절). 훅은 `--apply`
를 쓰지 않으니 코드를 몰래 바꾸지 않으며, 안내가 뜨면 대상의 `.gitignore` 에 `.heal/`
을 추가하십시오.

**주의**: `chroma_db`(학습 메모리)는 이 폴더 기준 하나뿐이라, 여러 프로젝트를 같은
selfheal 로 돌리면 같은 언어끼리 패턴이 섞입니다. 분리하려면 폴더를 복사하십시오.
