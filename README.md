# TelescopeDAQ v0.1

TelescopeDAQ регистрирует осциллограммы телескопа на реальном CAEN DT5740D,
подключённом по USB, и сохраняет события в ROOT через `uproot`.

## Важное ограничение оборудования

DT5740D не поддерживает DPP-PHA. Установленная плата сейчас работает с
waveform/standard firmware `4.29/0.13`. Поэтому v0.1 использует стандартный
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

## Запуск канала 0

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
каналов. Для текущего генератора измерены baseline около 2193 и положительный
импульс до 4021 ADC, поэтому в тестовом YAML установлен rising threshold 2300.

## Использованные функции CAENDigitizer

Backend вызывает функции из установленного `CAENDigitizer.h`: `OpenDigitizer`,
`GetInfo`, `Reset`, `SetRecordLength`, `SetPostTriggerSize`, `SetGroupEnableMask`,
`SetGroupDCOffset`, `SetGroupTriggerThreshold`, `SetTriggerPolarity`,
`SetGroupSelfTrigger`, `MallocReadoutBuffer`, `AllocateEvent`, `ReadData`,
`GetNumEvents`, `GetEventInfo`, `DecodeEvent`, `SWStartAcquisition`,
`SWStopAcquisition`, `FreeEvent`, `FreeReadoutBuffer` и `CloseDigitizer`.

## v0.2

Планируются 16 каналов, coincidence, внешний и периодический триггеры,
расширенный монитор и, при переходе платы на соответствующую прошивку,
отдельный backend DPP-QDC.
