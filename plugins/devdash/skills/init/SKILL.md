---
name: init
description: 지금 프로젝트를 devdash 에 등록해 작업 기록(할 일 파일 · 자동 변경 기록)을 켠다. 사용자가 /devdash:init 으로 부를 때만 쓴다.
disable-model-invocation: true
argument-hint: "[표시 이름 — 따옴표 없이]"
allowed-tools: Bash(py -3 *devdash_store.py* register *)
---

# 프로젝트 등록

등록 결과:

!`py -3 "${CLAUDE_PLUGIN_ROOT}/scripts/devdash_store.py" register . "$ARGUMENTS"`

위 결과를 사용자에게 그대로 알린다 (경로는 `백틱` 으로 감싼다 — 감싸지 않으면 `\` 가 사라져 보인다).
그 밖의 일은 하지 않는다.

- 등록했으면: **다음 메시지부터** 이 프로젝트에서 작업 기록 규칙이 켜진다고 덧붙인다
  (훅이 "작업 기록 폴더 · 세션 이름" 을 알려 주면 그때부터 기록한다). 이미 등록된 프로젝트였어도 결과는 같다
- 등록하지 못했으면: 이유를 그대로 전하고, 무엇을 확인하면 되는지 한 줄로 말한다
