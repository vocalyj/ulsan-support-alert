name: check

on:
  schedule:
    - cron: "5,35 * * * *"
  workflow_dispatch:
    inputs:
      test:
        description: "테스트 결과를 텔레그램으로 받기 (true 또는 false)"
        default: "true"

permissions:
  contents: write

concurrency:
  group: check
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 브라우저 도구 설치
        run: |
          pip install playwright
          playwright install --with-deps chromium

      - name: 게시판 확인
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TEST_MODE: ${{ github.event.inputs.test }}
        run: python check.py

      - name: 확인한 글 기억하기
        run: |
          git config user.name "alert-bot"
          git config user.email "alert-bot@users.noreply.github.com"
          git add seen.json
          git diff --staged --quiet || (git commit -m "update seen" && git push)
