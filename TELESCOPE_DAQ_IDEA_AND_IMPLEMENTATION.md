# TelescopeDAQ: идея и реализация v0.1

## 1. Назначение проекта

TelescopeDAQ — программное обеспечение DAQ-компьютера телескопа регистрации широких атмосферных ливней. Система предназначена для получения оцифрованных сигналов SiPM с CAEN digitizer, сохранения исходных осциллограмм в ROOT-файлы и оперативного контроля регистрации.

Полный телескоп должен включать 16 SiPM-каналов, усилительную электронику и CAEN digitizer с USB-подключением. На первом этапе реализован минимальный устойчивый тракт для лабораторной проверки одного канала:

```text
Генератор → канал 0 DT5740D → USB → TelescopeDAQ → ROOT → анализ и график
```

Архитектура разделяет управление оборудованием, модель события, запись ROOT и мониторинг. Благодаря этому проект можно постепенно расширить до полноценной 16-канальной установки, не переписывая базовый цикл сбора данных.

## 2. Проверенная аппаратная конфигурация

| Параметр | Значение |
|---|---|
| Digitizer | CAEN DT5740D |
| Серийный номер | 13850 |
| Подключение | USB, link 0 |
| Разрядность ADC | 12 bit |
| Частота дискретизации | 62,5 MS/s |
| Период одного sample | 16 нс |
| ROC firmware | 04.29, Build 8716 |
| AMC firmware | 00.13, Build 8A15 |
| Тип прошивки | `STANDARD_FW` |
| CAENDigitizer DLL | 2.17.3 из установки WaveDump |

### Ограничение прошивки

Перед реализацией были проверены установленные библиотеки, headers и официальные примеры CAEN. Выяснено, что DT5740D не поддерживает DPP-PHA. Для семейства x740 доступны:

- стандартная waveform-прошивка;
- DPP-QDC для моделей x740D.

Поэтому TelescopeDAQ v0.1 использует стандартный waveform API. PHA-структуры и неподтверждённые сигнатуры функций не используются.

## 3. Основная идея архитектуры

Работа программы организована как последовательный конвейер:

1. Загрузка и проверка YAML-конфигурации.
2. Загрузка установленной `CAENDigitizer.dll`.
3. Открытие DT5740D по USB и чтение информации о плате.
4. Сброс и настройка digitizer.
5. Настройка канала 0 и аппаратного self-trigger.
6. Выделение буферов CAEN и запуск acquisition.
7. Чтение и декодирование событий.
8. Расчёт baseline, amplitude и charge.
9. Запись событий и waveform в ROOT.
10. Вывод online-статуса в терминал.
11. Безопасная остановка и освобождение всех ресурсов.

Основные компоненты:

| Компонент | Назначение |
|---|---|
| `config.py` | Чтение и проверка YAML-конфигурации |
| `caen_constants.py` | Точные ctypes-структуры и константы из CAEN headers |
| `caen_digitizer.py` | Управление реальным DT5740D через `CAENDigitizer.dll` |
| `event.py` | Модель события и вычисление характеристик импульса |
| `acquisition.py` | Главный цикл регистрации и управление ресурсами |
| `root_writer.py` | Запись ROOT через `uproot` и `awkward` |
| `monitor.py` | Консольный online monitor |
| `run_control.py` | Подготовка run и журналирование |
| `inspect_root.py` | Проверка содержимого ROOT-файла |
| `plot_waveforms.py` | Построение осциллограмм из ROOT |

## 4. Что реализовано в v0.1

### Работа с реальным CAEN

- Открытие DT5740D по USB.
- Получение модели, серийного номера, разрядности и версий прошивок.
- Проверка, что подключена ожидаемая модель DT5740.
- Сброс платы перед конфигурацией.
- Настройка режима software-controlled acquisition.
- Настройка длины waveform и положения триггера.
- Включение аппаратной группы, содержащей канал 0.
- Настройка группового DC offset.
- Настройка абсолютного 12-битного trigger threshold.
- Настройка полярности триггера.
- Включение аппаратного self-trigger.
- Выделение и освобождение readout/event buffers.
- Запуск, остановка и закрытие USB-соединения.

У DT5740 параметры enable, offset и threshold частично организованы по группам из восьми каналов. Эта особенность учтена в backend.

### Чтение событий

Для standard firmware используется подтверждённая структура:

```c
CAEN_DGTZ_UINT16_EVENT_t
```

Waveform канала 0 копируется из памяти CAEN в независимый массив NumPy до следующего чтения буфера.

32-битный `TriggerTimeTag` расширяется до монотонного 64-битного значения с учётом rollover.

### Характеристики импульса

Для каждого события вычисляются:

- `baseline` — среднее первых 100 samples;
- `amplitude` — отклонение пика от baseline с учётом полярности;
- `charge` — сумма отсчётов относительно baseline с учётом полярности.

Для положительного импульса:

```text
amplitude = max(waveform) - baseline
charge = Σ(waveform - baseline)
```

Для отрицательного импульса:

```text
amplitude = baseline - min(waveform)
charge = Σ(baseline - waveform)
```

### ROOT-запись

Создаётся файл:

```text
output/run_000001.root
```

В нём находится дерево `events` со следующими branches:

| Branch | ROOT-тип | Содержание |
|---|---|---|
| `event_id` | `uint64` | Последовательный номер события |
| `channel` | `uint16` | Номер канала |
| `timestamp` | `uint64` | Расширенный TriggerTimeTag |
| `trigger_type` | `uint16` | Код источника триггера |
| `baseline` | `float32` | Средний уровень baseline |
| `amplitude` | `float32` | Амплитуда импульса |
| `charge` | `float32` | Интеграл относительно baseline |
| `waveform` | `var * uint16` | Полная осциллограмма ADC |

Рядом с ROOT-файлом сохраняется копия YAML:

```text
output/run_000001_config.yaml
```

Это позволяет восстановить параметры конкретного измерения.

### Online monitor

Во время регистрации программа выводит:

- номер run;
- число событий;
- прошедшее время;
- event rate;
- текущий канал;
- timestamp последнего события;
- baseline;
- amplitude;
- charge;
- приблизительный размер ROOT-файла.

### Безопасное завершение

Работу можно остановить:

- сочетанием `Ctrl+C`;
- нажатием `Enter`;
- нажатием `Esc`.

При завершении программа последовательно:

1. останавливает acquisition;
2. закрывает ROOT-файл;
3. освобождает event buffer;
4. освобождает readout buffer;
5. закрывает соединение с CAEN;
6. сохраняет уже записанные события.

Каждая операция очистки изолирована, поэтому ошибка одного шага не должна мешать выполнению остальных.

## 5. Использованные функции CAENDigitizer

Сигнатуры взяты из установленных файлов `CAENDigitizer.h` и `CAENDigitizerType.h`.

Backend использует:

- `CAEN_DGTZ_OpenDigitizer`;
- `CAEN_DGTZ_GetInfo`;
- `CAEN_DGTZ_Reset`;
- `CAEN_DGTZ_SetRecordLength`;
- `CAEN_DGTZ_SetPostTriggerSize`;
- `CAEN_DGTZ_SetGroupEnableMask`;
- `CAEN_DGTZ_SetGroupDCOffset`;
- `CAEN_DGTZ_SetGroupTriggerThreshold`;
- `CAEN_DGTZ_SetTriggerPolarity`;
- `CAEN_DGTZ_SetAcquisitionMode`;
- `CAEN_DGTZ_SetGroupSelfTrigger`;
- `CAEN_DGTZ_SetSWTriggerMode`;
- `CAEN_DGTZ_SetExtTriggerInputMode`;
- `CAEN_DGTZ_SetIOLevel`;
- `CAEN_DGTZ_SetMaxNumEventsBLT`;
- `CAEN_DGTZ_MallocReadoutBuffer`;
- `CAEN_DGTZ_AllocateEvent`;
- `CAEN_DGTZ_ClearData`;
- `CAEN_DGTZ_SWStartAcquisition`;
- `CAEN_DGTZ_ReadData`;
- `CAEN_DGTZ_GetNumEvents`;
- `CAEN_DGTZ_GetEventInfo`;
- `CAEN_DGTZ_DecodeEvent`;
- `CAEN_DGTZ_SWStopAcquisition`;
- `CAEN_DGTZ_FreeEvent`;
- `CAEN_DGTZ_FreeReadoutBuffer`;
- `CAEN_DGTZ_CloseDigitizer`.

В установленном API используется функция `CAEN_DGTZ_MallocReadoutBuffer`, а не вымышленное имя `CAEN_DGTZ_AllocateReadoutBuffer`.

## 6. Результат проверки на реальном оборудовании

На канал 0 был подан сигнал с генератора. Программным триггером сначала были измерены фактические параметры сигнала:

| Характеристика | Результат |
|---|---:|
| Baseline | около 2192 ADC |
| Минимум | около 2119 ADC |
| Максимум | около 4021 ADC |
| Полярность импульса | положительная |
| Рабочий rising threshold | 2300 ADC |

После настройки положительной полярности и порога 2300 аппаратный self-trigger успешно зарегистрировал три тестовых события.

Статистика ROOT-файла:

| Параметр | Значение |
|---|---:|
| Число событий | 3 |
| Канал | 0 |
| Длина waveform | 2049 samples |
| Amplitude min | 1824,84 ADC |
| Amplitude max | 1832,28 ADC |
| Amplitude mean | 1828,62 ADC |

В конфигурации запрошено 2048 samples, однако firmware x740 округлила фактическую длину до 2049 samples согласно внутреннему формату упаковки.

Построенный график:

![Осциллограммы генератора, канал 0](output/run_000001_waveforms_ch0.png)

## 7. Структура проекта

```text
TelescopeDAQ/
├── telescopedaq/
│   ├── __init__.py
│   ├── config.py
│   ├── event.py
│   ├── caen_constants.py
│   ├── caen_digitizer.py
│   ├── acquisition.py
│   ├── root_writer.py
│   ├── monitor.py
│   ├── run_control.py
│   └── utils.py
├── configs/
│   └── channel0_generator_test.yaml
├── scripts/
│   ├── start_run.py
│   ├── inspect_root.py
│   └── plot_waveforms.py
├── output/
├── logs/
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 8. Установка зависимостей

Рекомендуется использовать виртуальное окружение проекта:

```powershell
cd "C:\Users\andrey.shalyugin\Desktop\CAEN DT5740\TelescopeDAQ"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Установлены следующие Python-зависимости:

- NumPy;
- Awkward Array;
- uproot;
- PyYAML;
- Matplotlib;
- Rich.

Также на DAQ-компьютере должны быть установлены 64-битные CAEN USB Driver и CAENDigitizer Library.

## 9. Запуск регистрации

```powershell
cd "C:\Users\andrey.shalyugin\Desktop\CAEN DT5740\TelescopeDAQ"

.\.venv\Scripts\python.exe scripts\start_run.py `
  --config configs\channel0_generator_test.yaml
```

Для короткой проверки можно ограничить число событий:

```powershell
.\.venv\Scripts\python.exe scripts\start_run.py `
  --config configs\channel0_generator_test.yaml `
  --max-events 10
```

Перед запуском необходимо закрыть WaveDump, CoMPASS и другие программы, использующие тот же USB digitizer.

## 10. Проверка ROOT-файла

```powershell
.\.venv\Scripts\python.exe scripts\inspect_root.py `
  output\run_000001.root
```

Скрипт выводит:

- список объектов ROOT;
- число событий;
- зарегистрированные каналы;
- первые пять событий;
- диапазон timestamp;
- статистику длины waveform;
- статистику amplitude.

## 11. Построение осциллограмм

```powershell
.\.venv\Scripts\python.exe scripts\plot_waveforms.py `
  output\run_000001.root `
  --channel 0 `
  --n 20
```

Результат сохраняется в:

```text
output/run_000001_waveforms_ch0.png
```

## 12. Основные параметры YAML

| Параметр | Назначение |
|---|---|
| `record_length_samples` | Запрошенная длина waveform |
| `pre_trigger_percent` | Доля samples до момента триггера |
| `dc_offset` | 16-битный групповой DAC offset |
| `thresholds_adc.0` | Абсолютный 12-битный порог канала 0 |
| `polarity` | `positive` или `negative` |
| `max_events` | Максимальное число событий run |
| `update_every_events` | Период обновления online monitor |

Порог standard firmware является абсолютным ADC-кодом. Он не является смещением относительно baseline, как в некоторых DPP-режимах.

## 13. Что планируется в v0.2

- Включение и калибровка всех 16 SiPM-каналов.
- Статистика rate, baseline и amplitude отдельно по каждому каналу.
- Coincidence/multiplicity trigger.
- Настраиваемое окно совпадений.
- Внешний триггер.
- Периодический программный триггер.
- Контроль переполнений и потерянных событий.
- Ротация ROOT-файлов по размеру или времени.
- Расширенная run metadata.
- Отдельный DPP-QDC backend после осознанного перехода DT5740D на соответствующую прошивку.
- Архитектурные точки подключения GPS, метеостанции, Boltek и all-sky camera без включения этих подсистем в ядро v0.1.

## 14. Критерий готовности v0.1

Версия v0.1 проверена как минимальный рабочий прототип:

1. Программа подключается к реальному DT5740D по USB.
2. Канал 0 запускает аппаратный self-trigger по сигналу генератора.
3. Полные waveform сохраняются в ROOT.
4. ROOT-файл читается отдельным диагностическим скриптом.
5. Из ROOT строится корректный PNG с осциллограммами.
6. При завершении ROOT-файл, CAEN buffers и USB-соединение закрываются безопасно.

Таким образом, основной тракт TelescopeDAQ подтверждён на реальном оборудовании и готов к поэтапному расширению.
