from enum import Enum

from ovos_config import Configuration
from ovos_plugin_manager.wakewords import OVOSWakeWordFactory, HotWordEngine
from ovos_utils.log import LOG
from ovos_utils.messagebus import FakeBus


class CyclicAudioBuffer:
    def __init__(self, duration=0.98, initial_data=None,
                 sample_rate=16000, sample_width=2):
        self.size = self.duration_to_bytes(duration, sample_rate, sample_width)
        initial_data = initial_data or self.get_silence(self.size)
        # Get at most size bytes from the end of the initial data
        self._buffer = initial_data[-self.size:]

    def clear(self):
        self._buffer = self.get_silence(self.size)

    @staticmethod
    def duration_to_bytes(duration, sample_rate=16000, sample_width=2):
        return int(duration * sample_rate) * sample_width

    @staticmethod
    def get_silence(num_bytes):
        return b'\0' * num_bytes

    def append(self, data):
        """Add new data to the buffer, and slide out data if the buffer is full
        Arguments:
            data (bytes): binary data to append to the buffer. If buffer size
                          is exceeded the oldest data will be dropped.
        """
        buff = self._buffer + data
        if len(buff) > self.size:
            buff = buff[-self.size:]
        self._buffer = buff

    def get(self):
        """Get the binary data."""
        return self._buffer


class HotwordState(str, Enum):
    """ current listener state """
    LISTEN = "wakeword"
    HOTWORD = "hotword"
    RECORDING = "recording"
    WAKEUP = "wakeup"


class HotwordContainer:
    _plugins = {}

    def __init__(self, bus=FakeBus(), expected_duration=3, sample_rate=16000, sample_width=2):
        self.bus = bus
        self.state = HotwordState.HOTWORD
        # used for old style non-streaming wakeword (deprecated)
        self.audio_buffer = CyclicAudioBuffer(expected_duration,
                                              sample_rate=sample_rate,
                                              sample_width=sample_width)

    def load_hotword_engines(self):
        LOG.info("creating hotword engines")
        config_core = Configuration()
        default_lang = config_core.get("lang", "en-us")
        hot_words = config_core.get("hotwords", {})
        global_listen = config_core.get("confirm_listening")
        global_sounds = config_core.get("sounds", {})

        main_ww = config_core.get("listener", {}).get("wake_word", "hey_mycroft").replace(" ", "_")
        wakeupw = config_core.get("listener", {}).get("stand_up_word", "wake_up").replace(" ", "_")

        for word, data in dict(hot_words).items():
            try:
                # normalization step to avoid naming collisions
                # TODO - move this to ovos_config package,
                #  on changes to the hotwords section this should be enforced directly
                # this approach does not fully solve the issue, config merging may be messed up
                word = word.replace(" ", "_")

                sound = data.get("sound")
                utterance = data.get("utterance")
                listen = data.get("listen", False)
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

    @property
    def ww_names(self):
        """ wakeup words exit sleep mode if detected after a listen word"""
        return list(self._plugins.keys())

    @property
    def plugins(self):
        return [v["engine"] for k, v in self._plugins.items()]

    @property
    def wakeup_words(self):
        """ wakeup words exit sleep mode if detected after a listen word"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if v.get("wakeup")}

    @property
    def listen_words(self):
        """ listen words trigger the VAD/STT stages"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if v.get("listen")}

    @property
    def stop_words(self):
        """ stop only work during recording mode, they exit recording mode"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if v.get("stopword")}

    @property
    def hot_words(self):
        """ hotwords only emit bus events / play sounds, they do not affect listening loop"""
        return {k: v["engine"] for k, v in self._plugins.items()
                if not v.get("stopword") and
                not v.get("wakeup") and
                not v.get("listen")}

    def found(self):
        if self.state == HotwordState.LISTEN:
            engines = self.listen_words
        elif self.state == HotwordState.WAKEUP:
            engines = self.wakeup_words
        elif self.state == HotwordState.RECORDING:
            engines = self.stop_words
        else:
            engines = self.hot_words

        # streaming engines will ignore the byte_data
        audio_data = self.audio_buffer.get()
        for ww_name, engine in engines.items():
            assert isinstance(engine, HotWordEngine)
            try:
                # non streaming ww engines expect a 3 second cyclic buffer here
                # streaming engines will ignore audio_data (got it via self.update)
                if engine.found_wake_word(audio_data):
                    return ww_name
            except:
                pass
        return None

    def get_ww(self, ww):
        meta = dict(self._plugins.get(ww))
        plug = meta["engine"]
        assert isinstance(plug, HotWordEngine)
        meta["key_phrase"] = ww
        meta["module"] = plug.config["module"]
        meta["engine"] = plug.__class__.__name__
        return meta

    def update(self, chunk):

        if self.state == HotwordState.LISTEN:
            engines = self.listen_words.values()
        elif self.state == HotwordState.WAKEUP:
            engines = self.wakeup_words.values()
        elif self.state == HotwordState.RECORDING:
            engines = self.stop_words.values()
        else:
            engines = self.hot_words.values()

        for engine in engines:
            try:
                # old style engines will ignore the update
                engine.update(chunk)
            except:
                pass

    def reset(self):
        self.audio_buffer.clear()
        for engine in self.plugins:
            try:
                engine.reset()
            except:
                pass

    def shutdown(self):
        for engine in self.plugins:
            try:
                engine.shutdown()
            except:
                pass
        for ww in self.ww_names:
            self._plugins.pop(ww)
