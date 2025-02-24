import os
import re
import time
from unittest import mock

import numpy as np
import pytest
import wandb
import wandb.sdk.lib.redirect
import wandb.util
from click.testing import CliRunner
from wandb.cli import cli
from wandb.sdk.lib import runid

console_modes = ["wrap"]
if os.name != "nt":
    console_modes.append("redirect")


def _wait_for_legacy_service_transaction_log():
    """Wait to allow for legacy-service to write the transaction log file.

    When legacy-service receives a shutdown request initiated by `run.finish()`,
    it sets an internal Event and responds immediately. The writer thread has
    a loop similar to this:

        while not event.is_set():
            try:
                record = self._input_q.get(timeout=1)
            except queue.Empty:
                continue
            self._process(record)

        self._finish()

    After the event is set, it may be an entire second before
    `input_q.get(timeout=1)` times out and `self._finish()` is invoked.
    The writer thread's `self._finish()` flushes the transaction log file,
    which is completely in-memory until this point in these tests.

    Within that time, the test's `run.finish()` or Run context manager returns.
    If the test tries to read the transaction log file too quickly, it observes
    an empty file and fails.

    NOTE: Prior to https://github.com/wandb/wandb/pull/9469, this sleep was
    not entirely necessary because of another 1-second wait in the client itself
    that followed a similar pattern as the above. Tests used to put a sleep
    before `run.finish()`, likely to allow the writer thread to process
    any buffered records if the CI worker is slow.
    """
    time.sleep(2)


@pytest.mark.parametrize("console", console_modes)
def test_run_with_console_redirect(user, capfd, console):
    tqdm = pytest.importorskip("tqdm")

    with capfd.disabled():
        with wandb.init(settings={"console": console}):
            print(np.random.randint(64, size=(40, 40, 40, 40)))  # noqa: T201

            for _ in tqdm.tqdm(range(100)):
                time.sleep(0.02)

            print("\n" * 1000)  # noqa: T201
            print("---------------")  # noqa: T201
            time.sleep(1)


@pytest.mark.parametrize("console", console_modes)
def test_offline_compression(user, capfd, console):
    tqdm = pytest.importorskip("tqdm")

    # map to old style wrap implementation until test is refactored
    if console == "wrap":
        console = "wrap_emu"
    with capfd.disabled():
        with wandb.init(settings={"console": console, "mode": "offline"}) as run:
            run_dir, run_id = run.dir, run.id

            for _ in tqdm.tqdm(range(100), ncols=139, ascii=" 123456789#"):
                time.sleep(0.05)

            print("\n" * 1000)  # noqa: T201

            print("QWERT")  # noqa: T201
            print("YUIOP")  # noqa: T201
            print("12345")  # noqa: T201

            print("\x1b[A\r\x1b[J\x1b[A\r\x1b[1J")  # noqa: T201

        _wait_for_legacy_service_transaction_log()

        binary_log_file = (
            os.path.join(os.path.dirname(run_dir), "run-" + run_id) + ".wandb"
        )
        binary_log = (
            CliRunner()
            .invoke(cli.sync, ["--view", "--verbose", binary_log_file])
            .stdout
        )

        # Only a single output record per stream is written when the run finishes
        re_output = re.compile(r"^Record: num: \d+\noutput {", flags=re.MULTILINE)
        assert len(re_output.findall(binary_log)) == 2

        # Only final state of progress bar is logged
        assert binary_log.count("#") == 100, binary_log.count

        # Intermediate states are not logged
        assert "QWERT" not in binary_log
        assert "YUIOP" not in binary_log
        assert "12345" not in binary_log
        assert "UIOP" in binary_log


@pytest.mark.parametrize("console", console_modes)
@pytest.mark.parametrize("numpy", [True, False])
@pytest.mark.timeout(300)
def test_very_long_output(user, capfd, console, numpy):
    # https://wandb.atlassian.net/browse/WB-5437
    with capfd.disabled():
        with mock.patch.object(
            wandb.wandb_sdk.lib.redirect,
            "np",
            wandb.sdk.lib.redirect._Numpy() if not numpy else np,
        ):
            with wandb.init(
                settings={
                    "console": console,
                    "mode": "offline",
                    "run_id": runid.generate_id(),
                }
            ) as run:
                run_dir, run_id = run.dir, run.id
                print("LOG" * 1000000)  # noqa: T201
                print("\x1b[31m\x1b[40m\x1b[1mHello\x01\x1b[22m\x1b[39m" * 100)  # noqa: T201
                print("===finish===")  # noqa: T201

            _wait_for_legacy_service_transaction_log()

            binary_log_file = (
                os.path.join(os.path.dirname(run_dir), "run-" + run_id) + ".wandb"
            )
            binary_log = (
                CliRunner()
                .invoke(cli.sync, ["--view", "--verbose", binary_log_file])
                .stdout
            )

            assert "\\033[31m\\033[40m\\033[1mHello" in binary_log
            assert binary_log.count("LOG") == 1000000
            assert "===finish===" in binary_log


@pytest.mark.parametrize("console", console_modes)
def test_no_numpy(user, capfd, console):
    with capfd.disabled():
        with mock.patch.object(
            wandb.wandb_sdk.lib.redirect,
            "np",
            wandb.sdk.lib.redirect._Numpy(),
        ):
            with wandb.init(settings={"console": console}) as run:
                print("\x1b[31m\x1b[40m\x1b[1mHello\x01\x1b[22m\x1b[39m")  # noqa: T201

            _wait_for_legacy_service_transaction_log()

            binary_log_file = (
                os.path.join(os.path.dirname(run.dir), "run-" + run.id) + ".wandb"
            )
            _ = (
                CliRunner()
                .invoke(cli.sync, ["--view", "--verbose", binary_log_file])
                .stdout
            )


@pytest.mark.parametrize("console", console_modes)
def test_memory_leak2(user, capfd, console):
    # map to old style wrap implementation until test is refactored
    if console == "wrap":
        console = "wrap_emu"
    with capfd.disabled():
        with wandb.init(settings={"console": console}) as run:
            for _ in range(1000):
                print("ABCDEFGH")  # noqa: T201
            time.sleep(3)
            assert len(run._out_redir._emulator.buffer) < 1000
