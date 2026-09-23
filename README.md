# TelescopeDAQ v0.3

Локальное веб-приложение **FastAPI + HTML/JavaScript** для сбора raw waveform
с **CAEN DT5740D**, USB, STANDARD waveform firmware. Запускается из терминала,
управляется в браузере. CLI и прежний Tkinter GUI сохранены.

## Быстрый запуск

Нужны Python 3.10+ x64, CAEN USB Driver и CAENDigitizer Library.
Закройте WaveDump, CoMPASS и другие программы, владеющие прибором.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/start_web.py
```

Откройте **http://127.0.0.1:8000**. Если порт занят, программа выбирает следующий
свободный из 20 портов и пишет точный адрес в лог терминала.
Подключение к CAEN происходит только по Connect или Start Run, не при открытии страницы.

```powershell
# Другой конфиг и начальный порт
.\.venv\Scripts\python.exe scripts/start_web.py --config configs/channel0_generator_test.yaml --port 8010

# Проверка интерфейса без оборудования
.\.venv\Scripts\python.exe scripts/start_web.py --demo
```

DEMO отмечен в интерфейсе и пишет синтетические данные только в `output/demo/`.
Это явный отдельный режим: ошибки настоящего CAEN не подменяются симуляцией.
DEMO External не генерирует импульсы и не подтверждает работу TRG-IN.

DLL ищется в установке CAEN, включая
`C:\Program Files\CAEN\Digitizers\WaveDump\bin\CAENDigitizer.dll`.
Можно задать полный путь в `CAEN_DIGITIZER_DLL`.
Реальный backend рассчитан на Windows; перенос драйверного загрузчика на Linux не выполнен.

## Что реализовано

- **Full Monitor**: ROOT-запись, статус, график числа событий в интервале за последние
  30 минут, отдельная вкладка Online Waveform с выбором нескольких каналов.
- **Write Only**: тот же формат и путь записи ROOT, без подготовки online waveform
  и обновления online-графиков. Предпочтительный режим длительной регистрации.
- **ROOT Viewer**: ранее закрытые файлы, выбранная waveform-запись, распределение
  физических событий по относительному аппаратному timestamp. Работает во время
  другого run; текущий записываемый файл открыть нельзя.
- **Settings**: проверка типов, диапазонов и сочетаний параметров в браузере и на
  сервере, импорт/экспорт YAML, атомарное сохранение, защита от устаревшей вкладки.
- **Threshold Scan**: нижний/верхний порог, шаг, время и лимит событий на точку;
  график `threshold ADC -> количество срабатываний`, выбор порога, CSV.
- Connect/Disconnect, Start/Stop, Emergency Stop, прогресс run/scan/upload и Logs.

Baseline, amplitude и charge намеренно не вычисляются, не отображаются и не
сохраняются. Этот выбор из предыдущей версии сохранён.

## Триггеры

| Режим | Реализация |
| --- | --- |
| Threshold | Self-trigger группы 0; trigger mask оставляет ch0. Порог абсолютный, 0..4095 ADC |
| External | TRG-IN, NIM/TTL, ведущий фронт, сохранение всех включённых каналов; self/software trigger отключены |
| Periodic | Software trigger с периодом `periodic.interval_s`, планирование по монотонным часам |

External теперь вызывает CAEN API и сверяет режим/логический уровень обратным
чтением. Обратный фронт и `save_all_enabled_channels: false` отклоняются явно.
**Физическая проверка новой веб-версии и TRG-IN на подключённом DT5740D не выполнена.**
Для стендовой проверки сначала согласуйте уровень NIM/TTL и электрические параметры
генератора с руководством прибора. Periodic не является real-time таймером.

## Данные и счётчики

Результат: `output/run_000001.root` и `output/run_000001_config.yaml`.
Повторный запуск с тем же Run ID блокируется: существующие данные не перезаписываются.

Дерево `events`: `event_id:uint64`, `channel:uint16`, `timestamp:uint64`,
`trigger_type:uint16`, `waveform:var * uint16`. Uproot также создаёт счётчик
`nwaveform`. Коды источников: 1 Threshold, 2 External, 3 Periodic.

Один физический trigger имеет общий `event_id` для waveform всех выбранных каналов.
Например, 100 triggers по 16 каналам дают 1600 waveform-записей:
**Accepted events = 100, Written waveforms = 1600**.
Скорость считается по реально прочитанным событиям, не по заданным 100 кГц генератора.

`monitor.rate_interval_s` задаёт скользящее окно подсчёта.
`monitor.waveform_update_interval_s` задаёт паузу между отображаемыми кадрами,
не между записываемыми событиями. В браузере показано `Display sampled`.
Короткие пики сохраняются при экранном прореживании; ROOT получает полный waveform.

Текущая программная конфигурация обслуживает каналы **0..15**.
Параметры DC offset и threshold аппаратно групповые, не независимые для каждого
канала. При шаге отсчёта 16 ns текущие 1024 samples дают окно 16.384 us.
Предел валидатора 196608 samples соответствует 3.145728 ms; доступная длина и
округление зависят от firmware/организации памяти и требуют проверки на приборе.
Единицы waveform samples нельзя автоматически переносить на TriggerTimeTag:
ROOT Viewer показывает сырые timestamp ticks без неподтверждённого пересчёта в секунды.

## Эксплуатация

Один процесс сервера владеет CAEN. Не используйте Uvicorn reload/multiple workers
и не запускайте параллельно аппаратные CLI/Tk GUI.
Несколько вкладок браузера видят один и тот же run. Изменения Settings и scan
во время записи блокируются сервером.

Закрытие браузера **не останавливает запись**. Stop завершает цикл и закрывает ROOT.
Emergency Stop выставляет тот же флаг остановки без ожидания пользователя, но не
прерывает зависший вызов DLL и не заменяет аппаратную защиту.
Для завершения нажмите **Safe Exit → Stop & Exit**. Новые команды блокируются,
текущий run дописывает принятые waveform, закрывает ROOT и освобождает CAEN.
Только после успешной очистки завершается HTTP-сервер. При ошибке сервер остаётся
доступным для Logs и не сообщает ложный успех. Ctrl+C также запускает очистку.

Терминал содержит logging, без дампов waveform и HTTP access-log каждого опроса.
Файл `logs/web.log` ротируется по 10 MiB, сохраняются пять архивов.
Интерфейс хранит последние 1000 сообщений.

### Просмотр по LAN

```powershell
.\.venv\Scripts\python.exe scripts/start_web.py --lan
```

На компьютере DAQ управление остаётся по `http://127.0.0.1:8000`.
На другом компьютере откройте **LAN viewer URL из терминала**, например
`http://192.168.1.20:8000` (замените пример фактическим IP и портом).
`127.0.0.1` на другом компьютере указывает на него самого, не на CAEN-компьютер.

LAN-клиентам доступны статус, графики, параметры, Logs и закрытые ROOT только
для чтения. Start/Stop/Exit, scan, изменение YAML и upload запрещены на сервере.
Проверяется адрес TCP-клиента, а не доверенные proxy headers.
Разрешены частные IPv4-диапазоны 10/8, 172.16/12, 192.168/16.
При необходимости настройте Windows Firewall только для доверенного частного
профиля и локальной подсети; программа правила Firewall не изменяет.
Без `--lan` сервер слушает только loopback.

LAN не имеет пароля и TLS: пользователи доверенной сети могут читать данные и
журнал. Не публикуйте сервер в Интернет, через reverse proxy или проброс портов.

## CLI и прежний GUI

```powershell
python scripts/start_run.py --config configs/channel0_generator_test.yaml --max-events 10
python scripts/start_gui.py --config configs/channel0_generator_test.yaml
python scripts/inspect_root.py output/run_000001.root
python scripts/plot_waveforms.py output/run_000001.root
```

Для каждого нового run сначала задайте незанятый Run ID.

## Проверка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests/browser_smoke.py --browser "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

Browser smoke использует временную папку и только синтетический источник.
Скриншоты сохраняются в `artifacts/`, не включаются в Git.
Для другого Chromium укажите его executable; без `--browser` требуется
установленный через Playwright Chromium.

## Документация

- [Полное описание программы](TELESCOPE_DAQ_PROGRAM_DESCRIPTION.md).
- [Пошаговый пользовательский мануал](docs/WEB_USER_MANUAL.md).
- [Иллюстрированный PDF со стрелками и номерами](docs/TelescopeDAQ_User_Manual.pdf).
- PDF воспроизводится командой `python scripts/build_user_manual.py` после
  установки `requirements-docs.txt`; исходные PNG в `docs` не изменяются.
- [Разбор миграции, архитектура и ограничения](docs/MIGRATION_ANALYSIS.md).
- [Первоначальная идея проекта](TELESCOPE_DAQ_IDEA_AND_IMPLEMENTATION.md), исторический документ.

Графики uPlot и иконки Lucide поставляются локально, CDN и Интернет для интерфейса
не нужны. Их лицензии лежат в `telescopedaq/web/static/vendor/`.
