# AGENTS.md

Правила для Codex, ChatGPT и других автоматизированных помощников в проекте `TelescopeDAQ`.

## Назначение

`TelescopeDAQ` является отдельным проектным Git-репозиторием для Python DAQ-комплекса телескопа с CAEN DT5740D через USB: acquisition, trigger modes, ROOT waveform recording, run control, online monitoring и offline ROOT viewer. Центральная координация хранится в `ai-research-workspace`, но исходный код, локальная проектная документация и подтвержденное состояние этого проекта остаются здесь.

## Обязательные правила

- Перед изменениями читать `AGENTS.md`, `PROJECT_CONTEXT.md`, `STATUS.md`, `RESEARCH_LOG.md`, `README.md`, `TELESCOPE_DAQ_PROGRAM_DESCRIPTION.md` и профильные документы, если задача их затрагивает.
- Не придумывать задачи, результаты, проценты готовности, версии программ, пути к оборудованию, ветки, commits или сроки.
- Не считать generated outputs, ROOT-файлы, logs, local configs и hardware-run artifacts частью чистого исходного состояния без проверки.
- Не удалять и не перезаписывать ROOT-файлы, waveform outputs, logs, local configs или hardware-run data без явного подтверждения пользователя.
- Не добавлять секреты, токены, пароли, персональные пути доступа, большие ROOT-файлы или production/user data в Git.
- Не выполнять `commit` и `push` без отдельного явного запроса пользователя.
- Если рабочее дерево уже содержит изменения, работать поверх них аккуратно и не откатывать чужие изменения.
- Если сведения в этом репозитории противоречат `ai-research-workspace`, остановиться и явно указать расхождение.

## Роли сред

- Local Workstation Agent: редактирование кода и документации, запуск тестов, локальный ROOT viewer, GUI checks и работа с CAEN только на устройстве, где оборудование и драйверы реально доступны.
- Linux Compute Agent: offline ROOT analysis, batch processing recorded outputs и вычислительные окружения; direct CAEN USB acquisition выполнять только если аппаратная среда подтверждена.
- ChatGPT: обсуждение, научный анализ, планирование, формулировки и координация.
- Codex: изменение файлов, тестирование, поддержание контекстных файлов и Git-операции только по запросу.

## Типичный цикл

```text
git pull -> прочитать контекст -> выполнить работу -> проверить результат -> обновить STATUS.md и RESEARCH_LOG.md -> commit -> push
```

`commit` и `push` выполняются только после отдельного подтверждения пользователя.

## Run/check policy

- Hardware acquisition с CAEN запускать только после явного подтверждения пользователя.
- Перед изменениями в backend проверять safety cleanup: остановку acquisition, закрытие ROOT-файла, освобождение CAEN buffers и закрытие USB-соединения.
- Перед изменениями GUI проверять, что write-only/root-writing path не зависит от частоты отрисовки.
- Для изменений Python-логики запускать доступные automated tests, если окружение позволяет.
