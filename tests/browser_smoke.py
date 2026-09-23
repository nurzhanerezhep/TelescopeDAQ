"""Real browser checks against an isolated synthetic DAQ, never against CAEN."""

from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import numpy as np
import uproot
import uvicorn
import yaml
from playwright.sync_api import expect, sync_playwright

from telescopedaq.config import load_config
from telescopedaq.event import Event
from telescopedaq.root_writer import RootWriter
from telescopedaq.web.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--browser",
        help="Path to a Chromium-based browser; otherwise Playwright Chromium",
    )
    args = parser.parse_args()
    artifacts = PROJECT / "artifacts" / ("web-ui-" + str(time.time_ns()))
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts) as temporary:
        workspace = Path(temporary)
        data = load_config(PROJECT / "configs/channel0_generator_test.yaml").data
        data["run"].update(run_id=1, output_dir="output", max_events=2000)
        data["caen"]["record_length_samples"] = 512
        data["monitor"]["waveform_update_interval_s"] = 0.8
        data["threshold"]["value_adc"] = 2200
        config = workspace / "config.yaml"
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        (workspace / "output").mkdir()
        archive = workspace / "output/archive.root"
        writer = RootWriter(archive, "none")
        writer.open()
        samples = (1800 + 1000 * np.exp(-(((np.arange(512) - 210) / 30) ** 2))).astype(
            np.uint16
        )
        writer.write_events([Event(i, 0, i * 500000, samples, 1) for i in range(20)])
        writer.close()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(
                    config,
                    workspace,
                    demo=True,
                    on_shutdown=lambda: setattr(server, "should_exit", True),
                ),
                log_level="warning",
                access_log=False,
            )
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=args.browser, headless=True
                )
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                page.goto(url)
                expect(page.locator("#settings-status")).to_have_text("Settings saved")
                # UI policy check; real peer-address authorization is covered by test_web.
                viewer = browser.new_page(viewport={"width": 1440, "height": 1050})

                def read_only_state(route):
                    response = route.fetch()
                    state = response.json()
                    state.update(read_only=True, lan_enabled=True)
                    route.fulfill(response=response, json=state)

                viewer.route("**/api/state", read_only_state)
                viewer.goto(url)
                expect(viewer.locator("#access-mode")).to_be_visible()
                for control in ("connect", "start", "safe-exit", "root-upload"):
                    expect(viewer.locator("#" + control)).to_be_disabled()
                viewer.locator('nav [data-view="settings"]').click()
                expect(viewer.locator('[name="caen.dc_offset"]')).to_be_disabled()
                viewer.screenshot(
                    path=str(artifacts / "web-lan-viewer.png"), full_page=True
                )
                viewer.close()
                page.locator("#connect").click()
                expect(page.locator("#state")).to_have_text("connected")
                page.locator("#disconnect").click()
                expect(page.locator("#state")).to_have_text("disconnected")
                page.locator('nav [data-view="settings"]').click()
                offset = page.locator('[name="caen.dc_offset"]')
                offset.fill("70000")
                expect(page.locator("#save-settings")).to_be_disabled()
                expect(page.locator("#settings-status")).to_have_class("invalid")
                offset.fill("32768")
                expect(page.locator("#save-settings")).to_be_enabled()
                page.locator("#save-settings").click()
                expect(page.locator("#settings-status")).to_have_text("Settings saved")
                page.screenshot(
                    path=str(artifacts / "web-settings-desktop.png"), full_page=True
                )
                page.locator('nav [data-view="run"]').click()
                page.locator("#daq-mode").select_option("full_monitor")
                page.locator("#start").click()
                expect(page.locator("#state")).to_have_text("running")
                page.wait_for_timeout(1100)
                page.screenshot(
                    path=str(artifacts / "web-run-desktop.png"), full_page=True
                )
                page.locator('nav [data-view="wave"]').click()
                expect(page.locator("#wave-metadata")).to_contain_text("ch0")
                page.get_by_label("Display channel 1", exact=True).check()
                expect(page.locator("#wave-metadata")).to_contain_text("ch1")
                pixels = page.locator("#wave-chart canvas").evaluate("""c => {
                    const data=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                    let count=0;for(let i=0;i<data.length;i+=4)if(data[i+3]>0)count++;return count;
                }""")
                assert pixels > 3000, pixels
                first = page.locator("#wave-metadata").inner_text()
                page.wait_for_timeout(1500)
                assert first != page.locator("#wave-metadata").inner_text(), (
                    "Preview did not advance"
                )
                page.screenshot(
                    path=str(artifacts / "web-wave-desktop.png"), full_page=True
                )
                page.locator("#wave-pause").click()
                first = page.locator("#wave-metadata").inner_text()
                page.wait_for_timeout(1200)
                assert first == page.locator("#wave-metadata").inner_text()
                page.locator('nav [data-view="viewer"]').click()
                page.locator("#root-files").select_option("output/archive.root")
                page.locator("#load-root").click()
                expect(page.locator("#offline-metadata")).to_contain_text("event 0")
                assert page.request.get(url + "/api/state").json()["busy"], (
                    "Viewer test must overlap acquisition"
                )
                page.locator("#next").click()
                expect(page.locator("#offline-metadata")).to_contain_text("event 1")
                page.locator("#stop").click()
                expect(page.locator("#state")).to_have_text("disconnected")
                status = page.request.get(url + "/api/state").json()["status"]
                assert status["total_events"] > 0
                assert status["written_events"] == status["total_events"] * 16
                page.locator("#root-upload").set_input_files(str(archive))
                expect(page.locator("#root-files")).to_have_value(
                    __import__("re").compile("output/imports/.+")
                )
                page.screenshot(
                    path=str(artifacts / "web-root-desktop.png"), full_page=True
                )
                page.locator('nav [data-view="settings"]').click()
                page.locator('[name="run.run_id"]').fill("2")
                expect(page.locator("#save-settings")).to_be_enabled()
                page.locator("#save-settings").click()
                expect(page.locator("#settings-status")).to_have_text("Settings saved")
                page.locator("#daq-mode").select_option("write_only")
                page.locator('nav [data-view="run"]').click()
                page.locator("#start").click()
                expect(page.locator("#state")).to_have_text("running")
                page.wait_for_timeout(1300)
                expect(page.locator("#run-chart-section")).to_be_hidden()
                assert page.request.get(url + "/api/waveforms").json()["channels"] == []
                page.screenshot(
                    path=str(artifacts / "web-write-only-desktop.png"), full_page=True
                )
                page.locator("#stop").click()
                expect(page.locator("#state")).to_have_text("disconnected")
                page.locator("#open-scan").click()
                for key, value in {
                    "lower": "2200",
                    "upper": "2220",
                    "step": "10",
                    "dwell_s": "0.1",
                    "max_events": "20",
                }.items():
                    page.locator(f'#scan-form [name="{key}"]').fill(value)
                page.locator("#scan-start").click()
                expect(page.locator("#scan-status")).to_contain_text("Finished")
                page.screenshot(
                    path=str(artifacts / "web-scan-desktop.png"), full_page=True
                )
                page.locator("#close-scan").click()
                page.set_viewport_size({"width": 390, "height": 844})
                for view in ("run", "wave", "settings", "viewer", "logs"):
                    page.locator(f'nav [data-view="{view}"]').click()
                    page.wait_for_timeout(300)
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), f"Mobile overflow: {view}"
                    page.screenshot(
                        path=str(artifacts / f"web-{view}-mobile.png"), full_page=True
                    )
                page.locator('nav [data-view="settings"]').click()
                page.locator('[name="run.run_id"]').fill("3")
                expect(page.locator("#save-settings")).to_be_enabled()
                page.locator("#save-settings").click()
                expect(page.locator("#settings-status")).to_have_text("Settings saved")
                page.locator('nav [data-view="run"]').click()
                page.locator("#start").click()
                expect(page.locator("#state")).to_have_text("running")
                page.wait_for_timeout(1100)
                page.locator("#safe-exit").click()
                page.locator("#cancel-exit").click()
                expect(page.locator("#state")).to_have_text("running")
                page.locator("#safe-exit").click()
                page.locator("#confirm-exit").click()
                expect(page.locator("#exit-status")).to_contain_text(
                    "Files closed", timeout=10000
                )
                page.screenshot(
                    path=str(artifacts / "web-safe-exit-mobile.png"), full_page=True
                )
                thread.join(10)
                assert not thread.is_alive(), (
                    "Safe Exit did not terminate the HTTP server"
                )
                with uproot.open(workspace / "output/demo/run_000003.root") as root:
                    assert root["events"].num_entries > 0
                    assert root["events"].num_entries % 16 == 0
                assert not errors, errors
                browser.close()
                print(
                    f"PASS: browser controls, acquisition/viewer isolation, 16 channels, pause, Write Only, scan, upload, mobile, LAN read-only UI, Safe Exit and ROOT finalization. Canvas: {pixels} painted pixels."
                )
        finally:
            server.should_exit = True
            thread.join(15)
            listener.close()
            assert not thread.is_alive(), "Server did not shut down"


if __name__ == "__main__":
    main()
