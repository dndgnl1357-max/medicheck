# 배포용 이미지. Hugging Face Spaces(Docker)와 Render 양쪽에서 그대로 쓴다.
#
# 이 앱은 DB 없이 CSV 를 메모리에 올려 쓴다. 그래서 이미지에 data/processed 가
# 들어 있어야 한다 — 배포 환경에는 공공데이터 API 키도 수집 스크립트도 없다.
# .gitignore / .dockerignore 에서 그 파일들만 예외로 둔 이유다.

FROM python:3.11-slim

WORKDIR /app

# 의존성을 먼저 깔아 레이어 캐시를 살린다 (코드만 고치면 재설치 안 함)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face Spaces 는 7860, Render 등은 $PORT 를 준다. 둘 다 받는다.
ENV PORT=7860
EXPOSE 7860

# 단일 워커. 데이터를 프로세스당 한 번 메모리에 올리므로 워커를 늘리면
# 메모리도 배로 든다. 무료 티어에서는 1개가 맞다.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
