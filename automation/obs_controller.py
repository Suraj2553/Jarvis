"""OBS launch and recording control."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import config

LOG = logging.getLogger(__name__)


class OBSController:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self._ws = None

    def start(self) -> None:
        if not config.OBS_ENABLED:
            LOG.info("OBS disabled in config.")
            return
        self._connect_websocket()
        if self._ws:
            self.switch_scene(config.OBS_SCENE_START)
            self.start_recording()
            return
        exe = self._find_obs()
        if exe is None:
            LOG.warning("OBS executable not found. Continuing without OBS automation.")
            return
        cmd = [str(exe), "--startrecording"]
        LOG.info("Launching OBS: %s", exe)
        self.proc = subprocess.Popen(cmd, cwd=str(exe.parent))
        time.sleep(config.OBS_STARTUP_WAIT)
        self._connect_websocket()
        self.switch_scene(config.OBS_SCENE_START)
        self.start_recording()

    def stop(self) -> None:
        if self._ws:
            try:
                self._ws.stop_record()
                LOG.info("OBS recording stopped via websocket.")
                time.sleep(2.0)
            except Exception as exc:
                LOG.warning("OBS websocket stop failed: %s", exc)
            try:
                self._ws.disconnect()
            except Exception:
                pass
        elif self.proc:
            LOG.info("OBS was started with --startrecording; please verify the recording stopped.")

    def switch_scene(self, scene_name: str) -> None:
        if not scene_name or not self._ws:
            return
        try:
            self._ws.set_current_program_scene(scene_name)
            LOG.info("OBS scene switched to %s", scene_name)
        except Exception as exc:
            LOG.debug("OBS scene switch failed: %s", exc)

    def start_recording(self) -> None:
        if not self._ws:
            return
        try:
            status = self._ws.get_record_status()
            if not getattr(status, "output_active", False):
                self._ws.start_record()
                LOG.info("OBS recording started via websocket.")
        except Exception as exc:
            LOG.debug("OBS start recording failed: %s", exc)

    def _connect_websocket(self) -> None:
        if not config.OBS_WEBSOCKET_ENABLED:
            return
        try:
            from obsws_python import ReqClient
            self._ws = ReqClient(
                host=config.OBS_WEBSOCKET_HOST,
                port=config.OBS_WEBSOCKET_PORT,
                password=config.OBS_WEBSOCKET_PASSWORD,
                timeout=4,
            )
            LOG.info("Connected to OBS websocket.")
        except Exception as exc:
            LOG.warning("OBS websocket unavailable: %s", exc)
            self._ws = None

    def _find_obs(self) -> Path | None:
        for path in config.OBS_EXE_CANDIDATES:
            if path.exists():
                return path
        return None
