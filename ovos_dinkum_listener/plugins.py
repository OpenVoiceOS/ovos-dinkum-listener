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
from queue import Queue
from typing import Any, Dict

from ovos_backend_client.api import STTApi, BackendType
from ovos_bus_client import MessageBusClient
from ovos_plugin_manager.stt import OVOSSTTFactory
from ovos_plugin_manager.templates.stt import StreamingSTT, StreamThread
from ovos_utils.log import LOG


class FlacStreamThread(StreamThread):

    def __init__(self, queue, language):
        super().__init__(queue, language)
        self._flac_proc = None
        self._start_flac()

    def finalize(self):
        """ return final transcription """
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
            self._stop_flac()

            return STTApi(backend_type=BackendType.OFFLINE).stt(flac, "en-US", 1)
        except Exception:
            LOG.exception("Error in STTApi")
        return None

    def handle_audio_stream(self, audio, language):
        for chunk in audio:
            self.update(chunk)

    def update(self, chunk: bytes):
        # Stream chunks into FLAC encoder
        assert self._flac_proc is not None
        assert self._flac_proc.stdin is not None

        self._flac_proc.stdin.write(chunk)

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


class FlacStreamingSTT(StreamingSTT):

    def create_streaming_thread(self):
        self.queue = Queue()
        return FlacStreamThread(self.queue, self.lang)


def load_stt_module(config: Dict[str, Any], bus: MessageBusClient) -> StreamingSTT:
    stt_config = config["stt"]
    plug = OVOSSTTFactory.create(stt_config)
    if not isinstance(plug, StreamingSTT):
        LOG.warning("dinkum only supports streaming STTs")
        LOG.info("Using FlacStreamingSTT wrapper -> ovos-backend-client.api.STTApi(backend_type=BackendType.OFFLINE)")
        return FlacStreamingSTT(config)
    return plug
