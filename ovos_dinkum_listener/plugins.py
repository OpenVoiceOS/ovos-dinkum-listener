import time
from typing import Any, Dict, Optional, List, Tuple, Union

from ovos_config.config import Configuration
from ovos_plugin_manager.stt import OVOSSTTFactory
from ovos_plugin_manager.templates.stt import StreamingSTT, StreamThread
from ovos_plugin_manager.utils import ReadWriteStream
from ovos_plugin_manager.utils.audio import AudioData
from ovos_utils.log import LOG


class FakeStreamThread(StreamThread):
    def __init__(self, queue, language, engine, sample_rate, sample_width):
        super().__init__(queue, language)
        self.buffer = ReadWriteStream()
        self.engine = engine
        self.sample_rate = sample_rate
        self.sample_width = sample_width

    def finalize(self):
        """return final transcription"""

        if not self.buffer:
            return ""

        try:
            # plugins expect AudioData objects
            audio = AudioData(
                self.buffer.read(),
                sample_rate=self.sample_rate,
                sample_width=self.sample_width,
            )
            transcript = self.engine.execute(audio, self.language)

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
    def __init__(self, engine, config=None):
        super().__init__(config)
        self.engine = engine

    def create_streaming_thread(self):
        listener = Configuration().get("listener", {})
        sample_rate = listener.get("sample_rate", 16000)
        sample_width = listener.get("sample_width", 2)
        return FakeStreamThread(
            self.queue, self.lang, self.engine, sample_rate, sample_width
        )

    def stream_start(self, language=None):
        """Discard audio left over from the previous session before starting.

        ``StreamingSTT.stream_start`` stops the old stream, and stopping a
        ``FakeStreamThread`` transcribes whatever its buffer still holds.
        Anything still there was never asked for via ``transcribe`` (the
        voice loop only reads the fallback engine when the primary fails),
        so it would be transcribed late, inside the voice loop thread, and
        for a server-backed engine that is a network round trip per wake.
        """
        if self.stream is not None:
            self.stream.buffer.clear()
        super().stream_start(language)

    def _wait_for_buffered_audio(self, timeout: float = 2.0) -> None:
        """Block until the stream thread has written every queued chunk.

        ``stream_data`` only enqueues; the thread copies chunks into the
        buffer on its own schedule. Reading the buffer before the queue is
        drained silently drops the tail of the utterance, and the dropped
        chunks are then transcribed at the next ``stream_start``.
        """
        queue, stream = getattr(self, "queue", None), self.stream
        if queue is None or stream is None:
            return
        deadline = time.monotonic() + timeout
        while queue.unfinished_tasks and stream.is_alive():
            if time.monotonic() >= deadline:
                LOG.warning(f"STT stream still has {queue.unfinished_tasks} "
                            f"unwritten chunk(s) after {timeout}s; "
                            "transcribing partial audio")
                return
            time.sleep(0.005)

    def transcribe(
        self,
        audio: Optional[Union[bytes, AudioData]] = None,
        lang: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """transcribe audio data to a list of
        possible transcriptions and respective confidences"""
        # plugins expect AudioData objects
        if audio is None:
            self._wait_for_buffered_audio()
            audiod = AudioData(
                self.stream.buffer.read(),
                sample_rate=self.stream.sample_rate,
                sample_width=self.stream.sample_width,
            )
            self.stream.buffer.clear()
        elif isinstance(audio, bytes):
            audiod = AudioData(
                audio,
                sample_rate=self.stream.sample_rate,
                sample_width=self.stream.sample_width,
            )
        elif isinstance(audio, AudioData):
            audiod = audio
        else:
            raise ValueError(
                f"'audio' must be 'bytes' or 'AudioData', got '{type(audio)}'"
            )
        LOG.debug(f"Transcribing with lang: {lang}")
        return self.engine.transcribe(audiod, lang)


def load_stt_module(config: Dict[str, Any] = None) -> StreamingSTT:
    """
    Load an STT module based on configuration
    @param config: STT or global configuration or None (uses Configuration)
    @return: Initialized StreamingSTT plugin
    """
    # Create a copy because we're setting default values here
    stt_config = config or Configuration().get("stt", {})
    stt_config = dict(stt_config)
    default_lang = Configuration().get("lang")
    stt_config.setdefault("lang", default_lang)
    if stt_config["lang"] != default_lang:
        LOG.warning(
            f"STT lang ({stt_config['lang']} differs from global "
            f"({Configuration.get('lang')}"
        )
    plug = OVOSSTTFactory.create(stt_config)
    if not isinstance(plug, StreamingSTT):
        LOG.debug(f"Using FakeStreamingSTT wrapper with config={config}")
        return FakeStreamingSTT(plug, config)
    return plug


def load_fallback_stt(cfg: Dict[str, Any] = None) -> Optional[StreamingSTT]:
    """
    Load a fallback STT module based on configuration
    @param cfg: STT or global configuration or None (uses Configuration)
    @return: Initialized StreamingSTT plugin if configured, else None
    """
    cfg = cfg or Configuration().get("stt", {})
    default_lang = Configuration().get("lang")
    fbm = cfg.get("fallback_module")
    if not fbm:
        return None
    try:
        config = cfg.get(fbm, {})
        config.setdefault("lang", default_lang)
        if config["lang"] != default_lang:
            LOG.warning(
                f"Fallback STT lang ({config['lang']} differs from "
                f"global ({Configuration.get('lang')}"
            )
        plug = OVOSSTTFactory.create({"module": fbm, fbm: config})
        if not isinstance(plug, StreamingSTT):
            LOG.debug(f"Using FakeStreamingSTT wrapper with config={config}")
            return FakeStreamingSTT(plug, config)
        return plug
    except Exception:
        LOG.exception("Failed to load fallback STT")
        return None
