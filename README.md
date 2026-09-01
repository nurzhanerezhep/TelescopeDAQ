# TelescopeDAQ v0.2

Поддерживаемая модель digitizer: **CAEN DT5740D**, USB, standard waveform
firmware. При подключении другой модели запуск блокируется до конфигурации платы.

TelescopeDAQ регистрирует осциллограммы телескопа на реальном CAEN DT5740D,
подключённом по USB, и сохраняет события в ROOT через `uproot`.

## Важное ограничение оборудования

DT5740D не поддерживает DPP-PHA. Установленная плата сейчас работает с
waveform/standard firmware `4.29/0.13`. Поэтому текущая версия использует стандартный
waveform API CAENDigitizer, а не PHA. Для x740D альтернативой является DPP-QDC.

## Установка

Нужны 64-bit Python и установленные CAEN USB Driver и CAENDigitizer Library.
Проверенная DLL находится по адресу:

```text
C:\Program Files\CAEN\Digitizers\WaveDump\bin\CAENDigitizer.dll
```

Если DLL расположена иначе, задайте `CAEN_DIGITIZER_DLL` полным путём. В Linux
понадобится адаптировать загрузчик и проверить `LD_LIBRARY_PATH`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Перед запуском закройте WaveDump, CoMPASS и другие программы, использующие USB.

## Запуск

```powershell
python scripts/start_run.py --config configs/channel0_generator_test.yaml
```

Остановка безопасна по `Ctrl+C`, `Enter` или `Esc`: acquisition останавливается,
буферы CAEN освобождаются, ROOT-файл и USB-соединение закрываются.

Короткий проверочный запуск:

```powershell
python scripts/start_run.py --config configs/channel0_generator_test.yaml --max-events 10
```

Результаты: `output/run_000001.root` и копия YAML рядом с ним.

## Графический online monitor

```powershell
python scripts/start_gui.py --config configs/channel0_generator_test.yaml
```

GUI содержит три режима:

- `Full Monitor` — запись ROOT, online waveform и event rate;
- `Write Only` — запись того же ROOT без передачи waveform в GUI и без перерисовки графиков;
- `ROOT Viewer` — независимый просмотр записанного файла, waveform выбранной записи
  и rate vs time.

`Full Monitor` содержит компактный dashboard, rolling rate и таблицу 16 каналов.
Waveform отображается только на отдельной вкладке `Online Waveform`, где можно
выбрать один или несколько каналов. Аппаратный backend записывает все выбранные
каналы `0..15`; один trigger получает общий `event_id` во всех channel-waveform.
Отдельная вкладка
`Settings` редактирует и валидирует параметры
Run, CAEN DT5740D, Channels, Trigger, Storage и Monitor. Сохранение YAML
выполняется атомарно через проверенный временный файл.
Ошибочные значения сразу подсвечиваются; Save, Connect и Start остаются
заблокированными до исправления всех полей.

Для DT5740D `record_length_samples` допускается до `196608` отсчётов на канал.
При периоде дискретизации `16 ns` это соответствует максимальному окну
`3.145728 ms`; текущее значение `2048` задаёт окно `32.768 µs`.

GUI перерисовывает только открытую вкладку, хранит только свежие online-кадры и
ограничивает историю лога. Отбрасывание устаревшей экранной копии не влияет на
acquisition и независимую запись всех событий в ROOT.

`monitor.waveform_update_interval_s` задаёт display holdoff от `0.05` до `60 s`.
За этот период GUI не рисует промежуточные события и при следующем обновлении
показывает самый свежий waveform. Во вкладке явно указано `ROOT: all events`:
прореживание относится только к экрану и не создаёт аппаратное dead time.

`monitor.rate_interval_s` (`0.1..3600 s`) задаёт окно подсчёта принятых событий.
GUI показывает `N events / interval`: значение `1` даёт события в секунду,
`60` — события за минуту. Это статистика событий DAQ, а не частота генератора.
ROOT Writer объединяет мелкие пачки до 1024 waveform или 16 MiB. Для высоких
частот рекомендуется `compression: none`, чтобы ZLIB не ограничивал acquisition.

Кнопка `Threshold Test` открывает отдельное окно сканирования порога канала 0.
Пользователь задаёт нижнюю и верхнюю границы, шаг, время измерения и максимум
событий на точку. Результат строится как график `threshold ADC -> количество
срабатываний`; выбранную на графике точку можно перенести в `Settings`.
Тест показывает waveform и число событий, не создаёт
ROOT и не может работать одновременно с основным acquisition. Проверенный порог
можно перенести в Settings без автоматической записи YAML.

Терминал, файл `logs/run_NNNNNN.log` и вкладка `Logs` используют единый Python
logging. Отдельные `print` и консольные таблицы не выводятся.

Верхняя панель позволяет проверить доступность CAEN, проверить конфигурацию,
запустить и штатно либо аварийно остановить run. В `Write Only` показываются
run ID, elapsed time, total/written events, события за интервал, размер ROOT, свободное
место, последний timestamp и число ошибок CAEN. Этот режим рекомендуется для
длительной регистрации.

Поддерживаются `threshold`, `external` и `periodic` trigger. Параметры берутся
из YAML. Настройка TRG-IN реализована для standard firmware, но polarity внешнего
входа должна быть проверена на реальном DT5740D. Текущий конфиг записывает
каналы `0..15`, а threshold trigger формируется группой канала 0.

## Проверка и график

```powershell
python scripts/inspect_root.py output/run_000001.root
python scripts/plot_waveforms.py output/run_000001.root --channel 0 --n 20
```

График сохраняется как `output/run_000001_waveforms_ch0.png`.

## Настройки YAML

- `record_length_samples` — длина осциллограммы;
- `pre_trigger_percent` — доля данных перед триггером;
- `thresholds_adc.0` — **абсолютный** 12-битный код порога standard firmware;
- `polarity` — `negative` или `positive`;
- `dc_offset` — групповой 16-битный DAC offset;
- `max_events` — число событий.

У x740 параметры offset, threshold и trigger polarity общие для группы из восьми
каналов. Для текущего генератора используется положительный
импульс до 4021 ADC, поэтому в тестовом YAML установлен rising threshold 2100.

## Использованные функции CAENDigitizer

Backend вызывает функции из установленного `CAENDigitizer.h`: `OpenDigitizer`,
`GetInfo`, `Reset`, `SetRecordLength`, `SetPostTriggerSize`, `SetGroupEnableMask`,
`SetGroupDCOffset`, `SetGroupTriggerThreshold`, `SetTriggerPolarity`,
`SetGroupSelfTrigger`, `MallocReadoutBuffer`, `AllocateEvent`, `ReadData`,
`GetNumEvents`, `GetEventInfo`, `DecodeEvent`, `SWStartAcquisition`,
`SWStopAcquisition`, `FreeEvent`, `FreeReadoutBuffer` и `CloseDigitizer`.

## Дальнейшее развитие

Планируются coincidence, аппаратные счётчики lost trigger/buffer occupancy и,
при переходе платы на соответствующую прошивку, отдельный backend DPP-QDC.
