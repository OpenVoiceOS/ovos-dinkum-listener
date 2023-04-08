# Copyright 2022 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
""" 'plugins' according to dinkum are not really pluggable """

import subprocess
import tempfile
from abc import ABCMeta, abstractmethod
from typing import Any, BinaryIO, Dict, Optional

from ovos_backend_client.api import STTApi
from ovos_bus_client import MessageBusClient
from ovos_utils.log import LOG


class DinkumHotWordEngine(metaclass=ABCMeta):
    def __init__(self, key_phrase="hey mycroft", config=None, lang="en-us"):
        self.config = config or {}

    @abstractmethod
    def found_wake_word(self, frame_data) -> bool:
        """frame_data is unused"""
        return False

    @abstractmethod
    def update(self, chunk):
        pass

    def shutdown(self):
        pass


class AbstractDinkumStreamingSTT(metaclass=ABCMeta):
    def __init__(self, bus: MessageBusClient, config):
        self.bus = bus
        self.config = config

    def start(self):
        pass

    @abstractmethod
    def update(self, chunk: bytes):
        pass

    @abstractmethod
    def stop(self) -> Optional[str]:
        pass

    def shutdown(self):
        pass


class DinkumRemoteSTT(AbstractDinkumStreamingSTT):
    def __init__(self, bus: MessageBusClient, config):
        super().__init__(bus, config)

        self._api = STTApi()
        self._flac_proc: Optional[subprocess.Popen] = None
        self._flac_file: Optional[BinaryIO] = None

    def start(self):
        self._start_flac()

    def update(self, chunk: bytes):
        # Stream chunks into FLAC encoder
        assert self._flac_proc is not None
        assert self._flac_proc.stdin is not None

        self._flac_proc.stdin.write(chunk)

    def stop(self) -> Optional[str]:
        try:
            assert self._flac_proc is not None
            assert self._flac_file is not None

            # Read contents of encoded file.
            #
            # A file is needed here so the encoder can seek back and write the
            # length.
            self._flac_proc.communicate()
            self._flac_file.seek(0)
            flac = self._flac_file.read()

            self._flac_file.close()
            self._flac_file = None

            self._flac_proc = None

            return self._api.stt(flac, "en-US", 1)
        except Exception:
            LOG.exception("Error in Mycroft STT")

        return None

    def _start_flac(self):
        self._stop_flac()

        # pylint: disable=consider-using-with
        self._flac_file = tempfile.NamedTemporaryFile(suffix=".flac", mode="wb+")

        # Encode raw audio into temporary file
        self._flac_proc = subprocess.Popen(
            [
                "flac",
                "--totally-silent",
                "--best",
                "--endian=little",
                "--channels=1",
                "--bps=16",
                "--sample-rate=16000",
                "--sign=signed",
                "-f",
                "-o",
                self._flac_file.name,
                "-",
            ],
            stdin=subprocess.PIPE,
        )

    def _stop_flac(self):
        if self._flac_proc is not None:
            # Try to gracefully terminate
            self._flac_proc.terminate()
            self._flac_proc.wait(0.5)
            try:
                self._flac_proc.communicate()
            except subprocess.TimeoutExpired:
                self._flac_proc.kill()

            self._flac_proc = None


def load_stt_module(config: Dict[str, Any], bus: MessageBusClient) -> AbstractDinkumStreamingSTT:
    stt_config = config["stt"]
    module_name = stt_config["module"]
    if "coqui" in module_name:
        LOG.debug("Using Dinkum Coqui STT")
        from mycroft_dinkum_listener.plugins.stt_coqui import CoquiStreamingSTT
        return CoquiStreamingSTT(bus, config)

    elif "vosk" in module_name:
        LOG.debug("Using Dinkum Vosk STT")
        from mycroft_dinkum_listener.plugins.stt_vosk import VoskStreamingSTT
        return VoskStreamingSTT(bus, config)

    elif "mycroft" not in module_name:
        LOG.warning("dinkum does not follow plugin standards, choose one of 'mycroft'/'coqui'/'vosk'")

    LOG.debug("Using Dinkum Remote STT (ovos-backend-client)")
    return DinkumRemoteSTT(bus, config)
