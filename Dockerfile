# selfheal 실행 환경입니다.
#
# 이 에이전트는 **대상 코드의 테스트를 실제로 돌려서** 성공을 판정합니다.
# 그래서 이미지 안에 에이전트의 런타임(파이썬)뿐 아니라
# **고칠 대상 언어의 툴체인**도 들어 있어야 합니다.
# Node 를 함께 깔아 두는 이유가 그것입니다. (Go 를 쓰려면 아래 주석을 푸십시오.)
#
#   docker build -t selfheal .
#   docker run --rm \
#     -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION=us-east-1 \
#     -v "$PWD/chroma_db:/app/chroma_db" \
#     selfheal ./data/samples/py-index
#
# chroma_db 를 볼륨으로 빼는 것이 중요합니다.
# 버그 패턴 메모리는 컨테이너를 넘어 **누적되어야** 학습의 의미가 생깁니다.

FROM python:3.12-slim

# 대상 언어 툴체인 — JS 샘플을 고치려면 node 가 필요합니다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm git \
    && rm -rf /var/lib/apt/lists/*

# Go 샘플까지 다루려면 아래를 활성화합니다. (이미지가 커집니다)
# RUN apt-get update && apt-get install -y --no-install-recommends golang-go \
#     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 레이어를 먼저 굳혀 두면 소스만 바뀔 때 재빌드가 빠릅니다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 메모리는 볼륨으로 마운트하는 자리입니다.
VOLUME ["/app/chroma_db"]

# 자격증명 없이도 배선 검증은 됩니다:
#   docker run --rm --entrypoint python selfheal -m pytest tests/test_wiring.py -q
ENTRYPOINT ["./run.sh"]
CMD ["--help"]
