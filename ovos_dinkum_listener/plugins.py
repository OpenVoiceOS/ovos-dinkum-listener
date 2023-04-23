from typing import Any, Dict

from ovos_bus_client import MessageBusClient
from ovos_plugin_manager.stt import OVOSSTTFactory
from ovos_plugin_manager.templates.stt import StreamingSTT, StreamThread
from ovos_plugin_manager.utils import ReadWriteStream
from ovos_config import Configuration
from ovos_utils.log import LOG
from speech_recognition import AudioData


class FakeStreamThread(StreamThread):

    def __init__(self, queue, language, engine, sample_rate, sample_width):
        super().__init__(queue, language)
        self.lang = language
        self.buffer = ReadWriteStream()
        self.engine = engine
        self.sample_rate = sample_rate
        self.sample_width = sample_width

    def finalize(self):
        """ return final transcription """
        try:
            # plugins expect AudioData objects
            audio = AudioData(self.buffer.read(),
                              sample_rate=self.sample_rate,
                              sample_width=self.sample_width)
            transcript = self.engine.execute(audio, self.lang)

            self.buffer.clear()
            return transcript
        except Exception:
            LOG.exception(f"Error in STT plugin: {self.engine.__class__.__name__}")
        return None

    def handle_audio_stream(self, audio, language):
        for chunk in audio:
            self.update(chunk)

    def update(self, chunk: bytes):
        self.buffer.write(chunk)


class FakeStreamingSTT(StreamingSTT):
    def __init__(self, config=None):
        super().__init__(config)
        self.engine = OVOSSTTFactory.create()

    def create_streaming_thread(self):
        listener = Configuration().get("listener", {})
        sample_rate = listener.get("sample_rate", 16000)
        sample_width = listener.get("sample_width", 2)
        return FakeStreamThread(self.queue, self.lang, self.engine, sample_rate, sample_width)


def load_stt_module(config: Dict[str, Any], bus: MessageBusClient) -> StreamingSTT:
    stt_config = config["stt"]
    plug = OVOSSTTFactory.create(stt_config)
    if not isinstance(plug, StreamingSTT):
        LOG.debug("Using FakeStreamingSTT wrapper")
        return FakeStreamingSTT(config)
    return plug
