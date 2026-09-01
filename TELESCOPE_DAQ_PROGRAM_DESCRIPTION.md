# TelescopeDAQ: полное описание программы

## 1. Назначение

TelescopeDAQ — программа сбора и анализа осциллограмм с digitizer CAEN DT5740D,
подключённого к компьютеру по USB. Программа предназначена для лабораторной
проверки детекторного канала и дальнейшего развития в систему регистрации
событий телескопа.

Текущая версия выполняет следующие основные задачи:

- подключается к реальному CAEN DT5740D через `CAENDigitizer.dll`;
- настраивает выбранные каналы `0..15` и источник trigger;
- читает и декодирует события из CAEN readout buffer;
- сохраняет полные raw waveform и идентификаторы в ROOT;
- показывает online-состояние run и последнюю осциллограмму;
- поддерживает облегчённый режим длительной записи без online-графики;
- открывает и анализирует ранее записанные ROOT-файлы.

Основная конфигурация находится в файле
`configs/channel0_generator_test.yaml`.

## 2. Общая схема работы

```text
CAEN DT5740D
    |
    | USB / CAENDigitizer.dll
    v
CAENDigitizer: чтение и декодирование
    |
    v
Event: waveform, channel, timestamp, trigger type
    |
    v
RootWriter: постоянная запись ROOT
    |
    +--> AcquisitionStatus: компактное состояние run
    |
    +--> Full Monitor: копия последнего события для GUI
```

Acquisition является основным владельцем аппаратного цикла. ROOT Writer не
зависит от GUI и вызывается до публикации данных для отображения. Поэтому
закрытие, задержка или отсутствие online-графики не меняет структуру записанного
ROOT-файла.

## 3. Структура проекта

| Файл | Назначение |
|---|---|
| `telescopedaq/config.py` | Загрузка и проверка YAML, runtime-переопределение режимов |
| `telescopedaq/caen_constants.py` | Константы и `ctypes`-структуры CAEN API |
| `telescopedaq/caen_digitizer.py` | Управление DT5740D и декодирование событий |
| `telescopedaq/event.py` | Модель события и расчёт параметров импульса |
| `telescopedaq/acquisition.py` | Независимый цикл регистрации и публикация статуса |
| `telescopedaq/root_writer.py` | Запись дерева `events` в ROOT |
| `telescopedaq/monitor.py` | Компактный консольный monitor |
| `telescopedaq/gui.py` | Графический интерфейс и online-отображение |
| `telescopedaq/root_viewer.py` | Независимое чтение и анализ ROOT |
| `telescopedaq/run_control.py` | Подготовка run, logging и запуск acquisition |
| `scripts/start_gui.py` | Запуск GUI |
| `scripts/start_run.py` | Запуск DAQ без GUI |
| `scripts/inspect_root.py` | Текстовая проверка ROOT-файла |
| `scripts/plot_waveforms.py` | Сохранение waveform-графика в PNG |
| `tests/test_modes.py` | Автоматические проверки режимов и ROOT-записи |

## 4. Модель события

Класс `Event` содержит:

| Поле | Описание |
|---|---|
| `event_id` | Последовательный номер события |
| `channel` | Номер канала digitizer |
| `timestamp` | Расширенный CAEN `TriggerTimeTag` |
| `waveform` | Массив 16-битных ADC-отсчётов |
| `trigger_type` | Код источника trigger |

## 5. Режимы DAQ

### 5.1. Full Monitor

В режиме `full_monitor` программа:

- читает события с CAEN;
- записывает исходный waveform и идентификаторы каждого события в ROOT;
- публикует компактный статус run;
- передаёт GUI последнее событие каждой прочитанной пачки;
- показывает online waveform;
- показывает timestamp последнего события;
- обновляет число принятых событий за заданный интервал.

Для снижения нагрузки GUI получает только последнее waveform каждого канала в
пачке. Baseline, amplitude и charge не вычисляются и не сохраняются. Все
исходные waveform записываются в ROOT.

### 5.2. Write Only

В режиме `write_only` программа:

- читает те же события тем же acquisition loop;
- использует тот же `RootWriter`;
- сохраняет ту же структуру ROOT;
- не вызывает waveform callback;
- не обновляет Matplotlib canvas;
- показывает только компактный статус.

Статус содержит:

- run ID;
- elapsed time;
- total events;
- число принятых событий за заданный интервал;
- written events;
- размер ROOT-файла;
- свободное место на диске;
- последний timestamp;
- количество ошибок CAEN;
- текущее состояние acquisition.

`Write Only` является рекомендуемым режимом для длительной стабильной
регистрации, поскольку графическая отрисовка не использует процессорное время и
не создаёт очередь waveform.

### 5.3. ROOT Viewer

Режим `root_viewer` не запускает acquisition. Он предназначен для анализа уже
записанных ROOT-файлов и предоставляет:

- выбор ROOT-файла;
- выбор события по номеру entry;
- просмотр waveform выбранного события;
- число событий по временным интервалам timestamp.

Чтение summary и отдельных waveform выполняется в worker-потоках. Поэтому ROOT
Viewer не блокирует Tkinter main loop и не вмешивается в работающий DAQ.

## 6. Режимы trigger

Выбор режимов DAQ и trigger выполняется в верхней панели и относится только к
конкретному запуску. Вкладка `Settings` независимо хранит параметры CAEN,
каналов и всех трёх типов trigger. Переключение режима не изменяет threshold,
offset, polarity, periodic interval или параметры external trigger.

Online waveform находится только в отдельной вкладке. Пользователь может
выбрать один или несколько каналов; GUI хранит по одному последнему событию
каждого канала. Аппаратный backend декодирует и записывает выбранные каналы
`0..15`. Waveform одного физического trigger имеют общий `event_id`.

Очереди waveform и status ограничены, поэтому задержка GUI не может
остановить acquisition. При перегрузке заменяется только устаревшая экранная
копия, а ROOT Writer продолжает получать исходные события. Matplotlib
перерисовывает только активную вкладку, история GUI-лога ограничена.

Параметр `monitor.waveform_update_interval_s` (`0.05..60 s`) задаёт паузу между
экранными обновлениями waveform. В течение паузы GUI сохраняет только последний
полученный кадр. Это display holdoff, а не dead time digitizer: acquisition и
ROOT Writer продолжают обрабатывать все события. Текущий интервал и пометка
`ROOT: all events` постоянно показаны во вкладке `Online Waveform`.

Параметр `monitor.rate_interval_s` (`0.1..3600 s`) задаёт окно статистики.
Показатель равен числу физических событий, принятых DAQ за это окно: `1 s`
соответствует событиям в секунду, `60 s` — событиям за минуту. Он не обозначает
частоту генератора. ROOT Writer буферизует до 1024 waveform или 16 MiB перед
`tree.extend`. Для максимальной пропускной способности используется
`compression: none`.

### Threshold Test

Отдельная команда `Threshold Test` открывает независимое окно сканирования
порога. Для v0.1 доступен канал 0. Пользователь задаёт нижнюю и верхнюю границы,
шаг, время измерения и максимальное число событий на одну точку. Для каждого
значения инструмент очищает буфер, запускает acquisition и считает
срабатывания. Результат строится как график `X = threshold ADC`, `Y = количество
срабатываний`; рядом показывается последняя waveform. Инструмент временно
открывает и настраивает CAEN DT5740D в threshold mode, а после теста безопасно
закрывает USB-ресурсы. ROOT-файл не создаётся. Threshold Test, Connect probe и
основной acquisition взаимно блокируются. Выбранную на графике точку можно
перенести в Settings, но YAML сохраняется только кнопкой `Save YAML`.

### 6.1. Threshold trigger

В режиме `threshold`:

- включается аппаратный group self-trigger;
- external и software trigger отключаются;
- в версии v0.1 используется канал 0;
- значение берётся из `threshold.value_adc`;
- фронт выбирается по `channels.polarity`.

Порог DT5740D standard firmware является абсолютным 12-битным ADC-кодом в
диапазоне `0..4095`. Он не является смещением относительно baseline.

### 6.2. External trigger

В режиме `external`:

- аппаратный self-trigger отключается;
- software trigger отключается;
- CAEN `TRG-IN` переводится в режим запуска acquisition;
- проверяются `enabled`, `input` и `polarity`;
- в конфигурации предусмотрен `save_all_enabled_channels`.

Текущая версия декодирует и сохраняет выбранные каналы `0..15`. Параметр
`save_all_enabled_channels` предусмотрен для регистрации всех включённых
каналов. Polarity внешнего входа сохраняется и проверяется в конфигурации, но
требует отдельной аппаратной проверки на реальном DT5740D со standard firmware.

### 6.3. Periodic / Time trigger

В режиме `periodic`:

- аппаратный self-trigger отключается;
- внешний trigger отключается;
- включается CAEN software trigger;
- acquisition использует монотонный системный таймер;
- через каждый `periodic.interval_s` вызывается
  `CAEN_DGTZ_SendSWtrigger`.

Этот режим предназначен для периодической записи waveform по
расписанию.

## 7. Работа с CAEN DT5740D

Backend `CAENDigitizer` выполняет:

1. Поиск `CAENDigitizer.dll` в переменной `CAEN_DIGITIZER_DLL` и стандартных
   каталогах CAEN.
2. Загрузку DLL через `ctypes`.
3. Объявление используемых функций CAEN API.
4. Открытие USB-соединения.
5. Чтение модели, serial number, firmware и ADC bit depth.
6. Строгую проверку, что подключена именно модель CAEN DT5740D.
7. Сброс digitizer.
8. Настройку record length, pre-trigger, group mask, DC offset и trigger.
9. Выделение readout buffer и decoded event buffer.
10. Очистку старых данных и запуск acquisition.
11. Чтение, декодирование и преобразование waveform в NumPy.
12. Остановку и освобождение всех CAEN-ресурсов.

При переполнении 32-битного `TriggerTimeTag` программа увеличивает внутреннюю
epoch, сохраняя timestamp как расширенное 64-битное значение.

Каждый вызов CAEN API проверяется. Ненулевой код преобразуется в `CAENError` с
именем ошибки, если оно известно программе.

## 8. Acquisition loop

Перед началом run программа:

- создаёт output directory;
- сохраняет рядом с ROOT фактически применённую YAML-конфигурацию;
- создаёт CAEN backend и ROOT Writer;
- открывает, сбрасывает и настраивает digitizer;
- открывает ROOT-файл;
- запускает acquisition.

Основной цикл:

1. Проверяет запрос штатной или аварийной остановки.
2. Проверяет `Ctrl+C`, `Enter` и `Esc` для консольного запуска.
3. При periodic mode отправляет software trigger по таймеру.
4. Читает доступные события из CAEN.
5. Ограничивает пачку значением `max_events`.
6. Записывает события в ROOT.
7. Увеличивает `total_events` и `written_events`.
8. Обновляет последний timestamp.
9. Публикует консольный monitor, если он включён.
10. Только в `full_monitor` передаёт последнее событие GUI.
11. Публикует компактный `AcquisitionStatus`.

При завершении независимо закрываются acquisition, ROOT Writer, CAEN buffers и
USB handle. Ошибка одного этапа очистки не должна препятствовать выполнению
остальных этапов.

## 9. ROOT-файл

В каждом ROOT-файле создаётся дерево `events`:

| Branch | ROOT type | Значение |
|---|---|---|
| `event_id` | `uint64` | Номер события |
| `channel` | `uint16` | Канал |
| `timestamp` | `uint64` | Расширенный TriggerTimeTag |
| `trigger_type` | `uint16` | Код trigger |
| `waveform` | `var * uint16` | Полный waveform |

Рабочий конфиг использует `compression: none`, чтобы снизить CPU-нагрузку. При
необходимости можно выбрать ZLIB level 4. Имя файла формируется как
`output/run_NNNNNN.root`. Рядом создаётся
`output/run_NNNNNN_config.yaml` с фактическими runtime mode и trigger mode.

## 10. Графический интерфейс

GUI реализован на Tkinter и Matplotlib и оформлен как компактная тёмная рабочая
панель.

Вкладка Run Control является единым dashboard в обоих acquisition-режимах.
В `Full Monitor` главный dashboard содержит CAEN status, информацию run,
оконный счётчик событий, таблицу 16 каналов, основные счётчики и live log.
Waveform вынесен на отдельную вкладку. В `Write Only` остаются CAEN, Run Info,
числовая статистика и live log; графики не обновляются.

### Верхняя панель

- `DAQ Mode` — Full Monitor, Write Only или ROOT Viewer;
- `Trigger Mode` — Threshold, External или Periodic;
- `Config` — путь к YAML;
- `Output` — ожидаемый ROOT-файл;
- статус CAEN;
- номер run.

### Управление

- `Connect CAEN` — кратко открывает плату, читает информацию и закрывает probe;
- `Disconnect CAEN` — сбрасывает подтверждённое состояние подключения; во время
  run сначала требуется безопасная остановка;
- `Settings` открывает редактор threshold, polarity, record length,
  pre-trigger, DC offset, max events и periodic interval с явным сохранением YAML;
- `Start Run` — открывает и конфигурирует CAEN, ROOT и acquisition;
- `Stop Run` — отправляет запрос безопасной остановки;
- `Emergency Stop` — немедленно устанавливает тот же stop signal и явно
  фиксирует аварийную остановку в GUI и логе.

Принудительное завершение hardware thread не используется, поскольку оно может
оставить CAEN buffers, USB handle или ROOT-файл незакрытыми.

### Вкладки

- `Run Control` — информация CAEN и компактный статус записи;
- `Online Waveform` — последние осциллограммы выбранных каналов;
- `Run Statistics` — event rate;
- `ROOT Viewer` — offline-анализ ROOT;
- `Settings` — все параметры Run, CAEN DT5740D, Channels, Trigger, Storage и Monitor;
- `Logs` — журнал действий GUI и ошибок.

Settings содержит параметры acquisition. Аппаратно фиксированные `model: DT5740D`, USB,
firmware и обязательная ROOT/waveform запись доступны только для чтения.
DAQ mode и Trigger mode выбираются только в верхней панели и не изменяют
параметры Settings. YAML сначала записывается во временный файл, проходит
полную валидацию и только затем атомарно заменяет исходную конфигурацию.

Защита Settings работает на двух уровнях. При каждом вводе немедленно
проверяются тип, допустимый диапазон и локальные ограничения поля. Ошибочные
поля подсвечиваются, под формой выводятся до трёх конкретных причин, а Save,
Connect и Start блокируются. Перед сохранением дополнительно проверяется вся
структура и согласованность DAQ/trigger sections. Исходный YAML не изменяется,
если хотя бы одна проверка не пройдена.

Обмен между hardware thread и GUI выполняется через thread-safe очереди. Только
главный Tkinter thread изменяет элементы интерфейса и Matplotlib canvas.

Все сообщения acquisition проходят через стандартный Python `logging`. Один и
тот же поток сообщений направляется в терминал, `logs/run_NNNNNN.log` и вкладку
GUI `Logs`; отдельных `print` и Rich-таблиц acquisition не создаёт.

## 11. YAML-конфигурация

Текущая структура:

```yaml
run:
  run_id: 1
  mode: "channel0_generator_test"
  output_dir: "output"
  max_events: 10000

daq:
  mode: "full_monitor"      # full_monitor | write_only | root_viewer

caen:
  model: "DT5740D"
  connection: "USB"
  link_num: 0
  conet_node: 0
  vme_base_address: 0
  firmware: "STANDARD"
  record_length_samples: 2048
  pre_trigger_percent: 70
  dc_offset: 32768
  max_events_blt: 32

channels:
  n_channels: 16
  enabled: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
  polarity: "positive"

trigger:
  mode: "threshold"         # threshold | external | periodic

threshold:
  enabled: true
  channel: 0
  value_adc: 2100

external:
  enabled: false
  input: "TRG-IN"
  polarity: "rising"
  save_all_enabled_channels: true

periodic:
  enabled: false
  interval_s: 10

storage:
  write_root: true
  save_waveforms: true
  compression: "none"

monitor:
  enabled: true
  draw_waveforms: false
  update_every_events: 100
  waveform_update_interval_s: 1.0
  rate_interval_s: 1.0
```

GUI может временно переопределить `daq.mode` и `trigger.mode`. Остальные
параметры берутся из YAML. Перед запуском итоговая конфигурация повторно
валидируется.

## 12. Запуск

Требуются 64-битный Python, CAEN USB Driver и CAENDigitizer Library.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Запуск GUI:

```powershell
.\.venv\Scripts\python.exe scripts\start_gui.py `
  --config configs\channel0_generator_test.yaml
```

Консольный запуск:

```powershell
.\.venv\Scripts\python.exe scripts\start_run.py `
  --config configs\channel0_generator_test.yaml
```

Короткий тестовый run:

```powershell
.\.venv\Scripts\python.exe scripts\start_run.py `
  --config configs\channel0_generator_test.yaml --max-events 10
```

Проверка ROOT:

```powershell
.\.venv\Scripts\python.exe scripts\inspect_root.py output\run_000001.root
```

## 13. Автоматические проверки

Запуск тестов:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Реализованные тесты проверяют:

- запись одинакового числа ROOT entries в `Full Monitor` и `Write Only`;
- отсутствие event/waveform callback в `Write Only`;
- наличие event callback в `Full Monitor`;
- сохранение и чтение waveform;
- чтение scalar ROOT branches Offline Viewer;
- вызов software trigger в periodic mode;
- многоканальную запись с общим physical event ID;
- threshold scan и проверку его диапазона;
- валидацию интервалов monitor.

## 14. Фактические выводы GUI

GUI и status layer выводят:

- состояние системы;
- доступность или подключение CAEN;
- модель, serial number, firmware и ADC bit depth;
- run ID и выбранные режимы;
- elapsed time;
- total и written events;
- число принятых событий за настраиваемый интервал;
- размер ROOT-файла;
- свободное место на диске;
- последний timestamp;
- количество зарегистрированных CAEN errors;
- последние waveform выбранных каналов в Full Monitor;
- сообщения о подключении, запуске, остановке и ошибках.

## 15. Ограничения текущей версии

1. Polarity внешнего TRG-IN структурно предусмотрена, но требует аппаратной
   реализации и проверки для конкретной firmware.
2. Значение `threshold.value_adc` необходимо проверять относительно
   фактического baseline, поскольку threshold является абсолютным ADC-кодом.
3. Online statistics показывает количество принятых событий за окно
   `monitor.rate_interval_s`, а не частоту генератора или lost trigger.
4. Offline rate строится по равным интервалам raw timestamp и выводится как
   events per bin. Для точного Hz необходимо зафиксировать timestamp tick для
   используемой firmware.
5. Не реализованы температура, dead time, buffer occupancy и отдельные счётчики
   USB/readout overflow.
6. `Emergency Stop` является ускоренным cooperative stop, а не принудительным
   уничтожением hardware thread.
7. External trigger и устойчивость при высокой event rate ещё не проверены на
    реальном оборудовании.

## 16. Итоговое состояние

TelescopeDAQ имеет разделённые hardware, storage и presentation layers.
Acquisition не зависит от Tkinter, ROOT Writer работает независимо от online
graphics, а Offline Viewer использует отдельные worker-потоки.

Для длительных измерений следует использовать `Write Only`. Он записывает ROOT
тем же путём, что и `Full Monitor`, но не передаёт waveform GUI и не расходует
ресурсы на перерисовку.

`Full Monitor` подходит для настройки threshold, проверки формы импульса и
оперативного контроля rate. `ROOT Viewer` предназначен
для анализа результата после записи без вмешательства в acquisition.

Перед эксплуатационным запуском необходимо провести аппаратные тесты threshold,
TRG-IN, periodic trigger, timestamp resolution и поведения буферов при высокой
частоте событий.
