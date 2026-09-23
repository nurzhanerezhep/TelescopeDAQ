# RESEARCH_LOG.md

Журнал действий, решений и подтвержденного контекста проекта `TelescopeDAQ`.

## Entries

### 2026-09-23 - Safe web exit, LAN viewing and illustrated manual

- Context: requested safe application shutdown, viewing from other PCs on the same network and an annotated manual using six PNG screenshots in `docs`.
- Implementation: idempotent confirmed shutdown queued after the active CAEN-owner job; ROOT finalization precedes server exit. Failed cleanup remains visible in Logs. Shared logging/service lock avoids inverted lock ordering.
- Network: opt-in `--lan`, private IPv4 viewing only; nonlocal write requests are rejected regardless of forwarded headers. Localhost retains control. Windows Firewall rules were not changed.
- Documentation: README, full program description and Markdown manual updated. A 12-page Russian PDF adds numbered arrows and explanations without modifying original screenshots; its source builder and optional document dependencies are included.
- Verification: 30 automated unit tests passed; Edge/Playwright browser smoke passed with synthetic events, 16-channel ROOT recording, waveform rendering (45,013 painted pixels), independent viewer, Write Only, scan, uploads, mobile layout, read-only UI, and Safe Exit while recording. The finalized demo ROOT reopened successfully. Ruff F checks passed. All 12 PDF pages were rendered and visually checked.
- Limits: no physical CAEN run, TRG-IN validation or real two-PC LAN/firewall test in this update. Screenshots show a disconnected instrument and are not hardware measurements. No force-kill of a blocked driver is attempted; accepted data are finalized, unread device memory is not guaranteed to be drained.
- Git: new changes remain local; commit/push requires a separate explicit request. Historical import records below are retained.
- Next checks: operator-approved CAEN shutdown test and LAN access from a second trusted computer.

### 2026-09-07 - Research HQ tracking files added

```text
Date: 2026-09-07 14:33:18 MSK +0300
Project: TelescopeDAQ
Context: Проект подключен к модели Research HQ как подтвержденный локальный Git-репозиторий на текущем устройстве.
Decision or observation: Добавлены AGENTS.md, PROJECT_CONTEXT.md, STATUS.md и RESEARCH_LOG.md для фиксации правил работы агента, контекста проекта, состояния и истории. Код DAQ, конфигурации, тесты, output и logs не изменялись намеренно.
Device/environment: MacBook-Air-Nurzhan.local; Darwin 24.5.0 arm64.
Source: Local Git repository at /Users/nurzhanyerezhep/Desktop/git/TelescopeDAQ; remote https://github.com/nurzhanerezhep/TelescopeDAQ.git; branch main; HEAD fcc12437690770cef519669df2509361980132a8.
Next action: Проверить новые tracking files, уточнить Windows DAQ machine setup и правила хранения ROOT/output данных.
```

## Entry template

```text
Date:
Project:
Context:
Decision or observation:
Device/environment:
Source:
Next action:
```

## Rules

- Записывать только реальные действия, решения и проверенные результаты.
- Не переносить секреты, токены, пароли и персональные credential/cache значения.
- Не записывать неподтвержденные физические результаты как факты.
- Если запись основана на hardware run или ROOT analysis, указывать устройство, конфигурацию и источник данных.
