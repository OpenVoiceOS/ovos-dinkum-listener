from enum import Enum
from threading import Event
from typing import Optional

from ovos_config import Configuration
from ovos_plugin_manager.wakewords import OVOSWakeWordFactory, HotWordEngine
from ovos_utils.log import LOG
from ovos_utils.messagebus import FakeBus


class HotWordException(RuntimeWarning):
    """Exception related to HotWords"""


class CyclicAudioBuffer:
    def __init__(self, duration=0.98, initial_data=None,
                 sample_rate=16000, sample_width=2):
        self.size = self.duration_to_bytes(duration, sample_rate, sample_width)
        initial_data = initial_data or self.get_silence(self.size)
        # Get at most size bytes from the end of the initial data
        self._buffer = initial_data[-self.size:]

    def clear(self):
        """
        Set the buffer to empty data
        """
        self._buffer = self.get_silence(self.size)

    @staticmethod
    def duration_to_bytes(duration: float, sample_rate: int = 16000,
                          sample_width: int = 2) -> int:
        """
        Convert duration in seconds to a number of bytes
        @param duration: duration in seconds
        @param sample_rate: sample rate of expected audio
        @param sample_width: sample width of expected audio
        @return: number of bytes
        """
        return int(duration * sample_rate) * sample_width

    @staticmethod
    def get_silence(num_bytes: int) -> bytes:
        """
        Return null bytes
        @param num_bytes: number of bytes to return
        @return: requested number of null bytes
        """
        return b'\0' * num_bytes

    def append(self, data: bytes):
        """
        Add new data to the buffer, and slide out data if the buffer is full
        @param data: binary data to append to the buffer.
            If buffer size is exceeded, the oldest data will be dropped.
        """
        buff = self._buffer + data
        if len(buff) > self.size:
            buff = buff[-self.size:]
        self._buffer = buff

    def get(self) -> bytes:
        """
        Get the binary audio data from the buffer
        """
        return self._buffer


class HotwordState(str, Enum):
    """ current listener state """
    LISTEN = "wakeword"
    HOTWORD = "hotword"
    RECORDING = "recording"
    WAKEUP = "wakeup"


def _safe_get_plugins(func):
    def wrapped(*args, **kwargs):
        if not HotwordContainer._loaded.wait(30):
            raise TimeoutError("Timed out waiting for Hotwords load")
        try:
            return func(*args, **kwargs)
        except KeyError:
            raise HotWordException("Expected engine not loaded")

    return wrapped


class HotwordContainer:
    _plugins = {}
    _loaded = Event()

    def __init__(self, bus=FakeBus(), expected_duration=3, sample_rate=16000,
                 sample_width=2):
        self.bus = bus
        self.state = HotwordState.HOTWORD
        # used for old style non-streaming wakeword (deprecated)
        self.audio_buffer = CyclicAudioBuffer(expected_duration,
                                              sample_rate=sample_rate,
                                              sample_width=sample_width)
        self.reload_on_failure = False
        self.applied_hotwords_config = None

    def load_hotword_engines(self):
        """
        Load hotword objects from configuration
        """
        self._loaded.clear()
        LOG.info("creating hotword engines")
        config_core = Configuration()
        default_lang = config_core.get("lang", "en-us")
        hot_words = config_core.get("hotwords", {})
        self.applied_hotwords_config = hot_words
        global_listen = config_core.get("confirm_listening")
        global_sounds = config_core.get("sounds", {})

        main_ww = config_core.get("listener",
                                  {}).get("wake_word",
                                          "hey_mycroft").replace(" ", "_")
        wakeupw = config_core.get("listener",
                                  {}).get("stand_up_word",
                                          "wake_up").replace(" ", "_")

        for word, data in dict(hot_words).items():
            try:
                # normalization step to avoid naming collisions
                # TODO - move this to ovos_config package,
                #  on changes to the hotwords section this should be enforced directly
                # this approach does not fully solve the issue, config merging may be messed up
                word = word.replace(" ", "_")

                sound = data.get("sound")
                utterance = data.get("utterance")
                listen = data.get("listen", False) or word == main_ww
                wakeup = data.get("wakeup", False)
                stopword = data.get("stopword", False)
                trigger = data.get("trigger", False)
                lang = data.get("stt_lang", default_lang)
                enabled = data.get("active")
                event = data.get("bus_event")

                # automatically enable default wake words
                # only if the active status is undefined
                if enabled is None:
                    if word == main_ww or word == wakeupw:
                        enabled = True
                    else:
                        enabled = False

                # global listening sound
                if not sound and listen and global_listen:
                    sound = global_sounds.get("start_listening")

                if not enabled:
                    continue

                engine = OVOSWakeWordFactory.create_hotword(word, lang=lang)
                if engine is not None:
                    LOG.info(f"Loading hotword: {word} with engine: {engine}")
                    if hasattr(engine, "bind"):
                        engine.bind(self.bus)
                        # not all plugins implement this
                    if data.get('engine'):
                        LOG.info(f"Engine previously defined. "
                                 f"Deleting old instance.")
                        try:
                            data['engine'].stop()
                            del data['engine']
                        except Exception as e:
                            LOG.error(e)
                    self._plugins[word] = {"engine": engine,
                                           "sound": sound,
                                           "bus_event": event,
                                           "trigger": trigger,
                                           "utterance": utterance,
                                           "stt_lang": lang,
                                           "listen": listen,
                                           "wakeup": wakeup,
                                           "stopword": stopword}
            except Exception as e:
                LOG.error("Failed to load hotword: " + word)

        self._loaded.set()

        if not self.listen_words:
            LOG.error("No listen words loaded")
        else:
            self.reload_on_failure = True
        if not self.wakeup_words:
            LOG.warning("No wakeup words loaded")
        if not self.stop_words:
            LOG.warning("No stop words loaded")

    @property
    def ww_names(self):
        """ wakeup words exit sleep mode if detected after a listen word"""
        return list(self._plugins.keys())

    @property
    @_safe_get_plugins
    def plugins(self):
        return [v["engine"] for k, v in self._plugins.items()]

    @property
    @_safe_get_plugins
    def wakeup_words(self):
        """ wakeup words exit sleep mode if detected after a listen word"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if v.get("wakeup")}

    @property
    @_safe_get_plugins
    def listen_words(self):
        """ listen words trigger the VAD/STT stages"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if v.get("listen")}

    @property
    @_safe_get_plugins
    def stop_words(self):
        """ stop only work during recording mode, they exit recording mode"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if v.get("stopword")}

    @property
    @_safe_get_plugins
    def hot_words(self):
        """ hotwords only emit bus events / play sounds, they do not affect listening loop"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if not v.get("stopword") and
                not v.get("wakeup") and
                not v.get("listen")}

    def found(self) -> Optional[str]:
        """
        Check if a hotword is found in a relevant engine, based on self.state
        @return: string detected hotword, else None
        """
        # Check for which detectors we want; if none are active, log something
        # because it means there's no ww that "exits" the current state
        if self.state == HotwordState.LISTEN:
            engines = self.listen_words
            if not engines:
                raise HotWordException(
                    f"Waiting for listen_words but none are available!")
        elif self.state == HotwordState.WAKEUP:
            engines = self.wakeup_words
        elif self.state == HotwordState.RECORDING:
            engines = self.stop_words
        else:
            engines = self.hot_words

        # streaming engines will ignore the byte_data
        audio_data = self.audio_buffer.get()
        for ww_name, engine in engines.items():
            try:
                assert isinstance(engine, HotWordEngine)
                # non-streaming ww engines expect a 3-second cyclic buffer here
                # streaming engines will ignore audio_data
                # (got it via self.update)
                if engine.found_wake_word(audio_data):
                    LOG.debug(f"Detected wake_word: {ww_name}")
                    return ww_name
            except AssertionError:
                LOG.error(f"Expected HotWordEngine, but got: {engine} for "
                          f"{ww_name}")
                # TODO: Add engine reload here?
            except Exception as e:
                LOG.error(e)
        return None

    def get_ww(self, ww: str) -> dict:
        """
        Get information about the requested wake word
        @param ww: string wake word to get information for
        @return: dict wake word information
        """
        if ww not in self._plugins:
            raise ValueError(f"Requested ww not defined: {ww}")
        meta = dict(self._plugins.get(ww))
        plug = meta["engine"]
        assert isinstance(plug, HotWordEngine)
        meta["key_phrase"] = ww
        meta["module"] = plug.config["module"]
        meta["engine"] = plug.__class__.__name__
        return meta

    def update(self, chunk: bytes):
        """
        Update appropriate engines based on self.state
        @param chunk: bytes of audio to feed to hotword engines
        """
        self.audio_buffer.append(chunk)
        if self.state == HotwordState.LISTEN:
            # LOG.debug(f"Update listen_words")
            engines = self.listen_words.values()
        elif self.state == HotwordState.WAKEUP:
            # LOG.debug(f"Update wakeup_words")
            engines = self.wakeup_words.values()
        elif self.state == HotwordState.RECORDING:
            # LOG.debug(f"Update stop_words")
            engines = self.stop_words.values()
        else:
            # LOG.debug(f"Update hot_words")
            engines = self.hot_words.values()

        for engine in engines:
            try:
                # old style engines will ignore the update
                engine.update(chunk)
            except Exception as e:
                LOG.error(e)

    def reset(self):
        """
        Clear the audio_buffer and reset all hotword engines
        """
        self.audio_buffer.clear()
        for engine in self.plugins:
            try:
                # TODO: Remove check when default method is added to base class
                if hasattr(engine, 'reset'):
                    engine.reset()
            except Exception as e:
                LOG.error(e)

    def shutdown(self):
        """
        Shutdown all engines, remove references to plugins
        """
        for engine in self.plugins:
            try:
                engine.shutdown()
            except Exception as e:
                LOG.error(e)
        for ww in self.ww_names:
            self._plugins.pop(ww)
