# STATUS.md

Текущее состояние проекта фиксируется только на основе подтвержденной информации.

## Verified local update: 2026-09-23

- Implemented in the web application: confirmed Safe Exit, serialized CAEN cleanup, ROOT finalization, and rejection of new work during shutdown.
- Cleanup errors keep HTTP available for diagnostics; a blocked driver call is not forcibly interrupted.
- Optional `scripts/start_web.py --lan`: private IPv4 clients can view data; acquisition control and configuration changes remain loopback-only. No authentication or TLS is implemented.
- Illustrated Russian manual: `docs/TelescopeDAQ_User_Manual.pdf`, 12 pages, based on all six supplied screenshots. Original PNG files are unchanged. Builder: `scripts/build_user_manual.py`.
- Checks on Windows with Python 3.14: 30 unit tests passed; Edge/Playwright smoke passed, including Safe Exit during DEMO recording, reopening finalized ROOT, LAN read-only UI and mobile layout. Ruff undefined/unused-name checks passed.
- All 12 rendered PDF pages were visually reviewed.
- No CAEN hardware acquisition or two-computer LAN test was performed for this update. Network authorization was tested with simulated API client addresses, including spoofed forwarded headers.
- These changes are local pending explicit commit/push authorization. Import metadata below is historical, not the current checkout state.

## Research HQ import status

- State: `TRACKING FILES ADDED`.
- Date: 2026-09-07 14:33:18 MSK +0300.
- Device/environment: MacBook-Air-Nurzhan.local; Darwin 24.5.0 arm64.
- Scope of this update: added local agent/control Markdown files only.
- Code/data changes in this update: none intended.

## Git state at import

- Repository type: local Git repository.
- Local path: `/Users/nurzhanyerezhep/Desktop/git/TelescopeDAQ`.
- Remote: `https://github.com/nurzhanerezhep/TelescopeDAQ.git`.
- Branch: `main`.
- HEAD: `fcc12437690770cef519669df2509361980132a8`.
- Working tree: clean before these Research HQ tracking files were added.

## Project state recorded at import

- Technical/scientific state: partially documented in existing project files, not fully audited by Research HQ.
- Confirmed project role: Python DAQ system for CAEN DT5740D waveform acquisition, ROOT recording, run control, online monitoring and ROOT viewing.
- Confirmed supported digitizer from documentation: CAEN DT5740D with standard waveform firmware.
- Confirmed documented modes: `Full Monitor`, `Write Only`, `ROOT Viewer`; trigger modes `threshold`, `external`, `periodic`.
- Verification in this import: repository access and Git metadata were checked; no hardware acquisition, GUI run, ROOT inspection or tests were executed.

## Update rule

Update this file only after real work, confirmed checks, explicit user decisions, or verified imports from project files.
