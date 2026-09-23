# PROJECT_CONTEXT.md

Краткий подтвержденный контекст проекта `TelescopeDAQ`.

## Import metadata

- Imported for Research HQ tracking: 2026-09-07 14:33:18 MSK +0300.
- Device/environment: MacBook-Air-Nurzhan.local; Darwin 24.5.0 arm64.
- Repository type: local Git repository.
- Local path at import: `/Users/nurzhanyerezhep/Desktop/git/TelescopeDAQ`.
- Git remote at import: `https://github.com/nurzhanerezhep/TelescopeDAQ.git`.
- Branch at import: `main`.
- HEAD at import: `fcc12437690770cef519669df2509361980132a8`.
- Last commit observed at import: `fcc1243 2026-09-01T18:36:13+03:00 Add multichannel DAQ monitoring and optimized ROOT workflow`.
- Working tree at import: clean before Research HQ tracking files were added.

## Scope

Проект описан в `README.md` как TelescopeDAQ v0.3 для регистрации осциллограмм телескопа на CAEN DT5740D, подключенном по USB, с сохранением событий в ROOT через `uproot`. Основной интерфейс: FastAPI + HTML; CLI и прежний Tk GUI сохранены. Это описание кода, а не подтверждение аппаратного теста текущих изменений.

## Confirmed local structure

- `telescopedaq/config.py` - загрузка и проверка YAML-конфигурации.
- `telescopedaq/caen_constants.py` - constants and `ctypes` structures for CAEN API.
- `telescopedaq/caen_digitizer.py` - управление DT5740D and event decoding.
- `telescopedaq/event.py` - модель raw waveform-события; baseline, amplitude и charge не вычисляются и не хранятся.
- `telescopedaq/acquisition.py` - основной acquisition loop and status publishing.
- `telescopedaq/root_writer.py` - запись ROOT дерева `events`.
- `telescopedaq/monitor.py` - compact console monitor.
- `telescopedaq/gui.py` - graphical online monitor and settings UI.
- `telescopedaq/root_viewer.py` - independent ROOT file viewer.
- `telescopedaq/run_control.py` - run preparation, logging and acquisition startup.
- `telescopedaq/web/` - FastAPI handlers, single CAEN-owner worker, LAN read-only policy, offline analysis and HTML/JS interface.
- `scripts/start_web.py` - web startup; optional `--demo` and `--lan`.
- `scripts/build_user_manual.py` - illustrated PDF generation from unchanged source screenshots.
- `scripts/start_run.py` - DAQ run without GUI.
- `scripts/start_gui.py` - GUI startup.
- `scripts/inspect_root.py` - ROOT file inspection.
- `scripts/plot_waveforms.py` - waveform plotting to PNG.
- `configs/channel0_generator_test.yaml` - documented main/example YAML configuration.
- `tests/test_modes.py` - automated checks for modes and ROOT writing.
- `logs/.gitkeep`, `output/.gitkeep` - placeholders for local run logs and output directories.

## Confirmed workflows

- Web interface (local control): `python scripts/start_web.py`. Optional `--lan` permits read-only viewers from private IPv4 networks; the launcher prints their URL. No authentication/TLS: trusted networks only, no reverse proxy or Internet exposure.
- `Stop` finalizes the run but leaves the web server available. `Safe Exit` confirms the request, blocks new commands, waits for ROOT/CAEN cleanup, then terminates the launcher-managed server. Cleanup failure leaves Logs available. Closing a browser tab does not stop acquisition.
- Updated workflow and verification details: `STATUS.md`, `docs/WEB_USER_MANUAL.md` and `docs/TelescopeDAQ_User_Manual.pdf` (2026-09-23).

- Setup is documented for Python virtual environment and dependencies from `requirements.txt`.
- CAEN USB Driver and CAENDigitizer Library are required for hardware acquisition.
- A documented Windows DLL location is `C:\Program Files\CAEN\Digitizers\WaveDump\bin\CAENDigitizer.dll`; other locations can be supplied via `CAEN_DIGITIZER_DLL`.
- Basic run:

```powershell
python scripts/start_run.py --config configs/channel0_generator_test.yaml
```

- Short check run:

```powershell
python scripts/start_run.py --config configs/channel0_generator_test.yaml --max-events 10
```

- GUI/online monitor:

```powershell
python scripts/start_gui.py --config configs/channel0_generator_test.yaml
```

- Offline checks:

```powershell
python scripts/inspect_root.py output/run_000001.root
python scripts/plot_waveforms.py output/run_000001.root --channel 0 --n 20
```

## Confirmed model and hardware notes

- Supported digitizer model from README: CAEN `DT5740D`, USB, standard waveform firmware.
- README states the project blocks startup when another model is connected until board configuration is added.
- README states DT5740D does not support DPP-PHA in this setup; current code uses standard waveform API, with DPP-QDC mentioned as an alternative for x740D.
- GUI modes documented: `Full Monitor`, `Write Only`, `ROOT Viewer`.
- Trigger modes documented: `threshold`, `external`, `periodic`.
- ROOT Writer is documented as independent of GUI drawing and should record all selected events even when display updates are throttled.

## Confirmed storage cautions

- ROOT outputs, waveform data, logs and hardware-run artifacts should not be committed unless explicitly approved.
- Local hardware paths, CAEN DLL locations and DAQ environment details must be recorded only when confirmed.
- If sensitive local settings are needed, keep them outside Git or in explicitly ignored local files.

## Source files used for this context

- `README.md`
- `TELESCOPE_DAQ_PROGRAM_DESCRIPTION.md`
- `TELESCOPE_DAQ_IDEA_AND_IMPLEMENTATION.md`
- `pyproject.toml`
- `requirements.txt`
- Git metadata from local repository

## Open items for later import

- Confirm the actual Windows DAQ machine setup and CAEN driver/library installation.
- Confirm which YAML configurations are active for real telescope runs.
- Confirm policy for storing produced ROOT files and copied YAML configs.
- Confirm how TelescopeDAQ outputs will connect to later EAS analysis.
