# NEON AI (TM) SOFTWARE, Software Development Kit & Application Framework
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2022 Neongecko.com Inc.
# Contributors: Daniel McKnight, Guy Daniels, Elon Gasper, Richard Leeds,
# Regina Bloomstine, Casimiro Ferreira, Andrii Pernatii, Kirill Hrymailo
# BSD-3 License
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from ovos_plugin_manager.audio_transformers import find_audio_transformer_plugins
from ovos_utils.json_helper import merge_dict
from ovos_utils.log import LOG


class AudioTransformersService:

    def __init__(self, bus, config=None):
        self.config_core = config or {}
        self.loaded_plugins = {}
        self.has_loaded = False
        self.bus = bus
        # to activate a plugin, just add an entry to mycroft.conf for it
        self.config = self.config_core.get("audio_transformers") or {
          #  "ovos-audio-classifier-gender": {}
        }
        self.load_plugins()

    def load_plugins(self):
        for plug_name, plug in find_audio_transformer_plugins().items():
            if plug_name in self.config:
                # if disabled skip it
                if not self.config[plug_name].get("active", True):
                    continue
                try:
                    self.loaded_plugins[plug_name] = plug()
                    LOG.info(f"loaded audio transformer plugin: {plug_name}")
                except Exception as e:
                    LOG.exception(f"Failed to load audio transformer plugin: {plug_name}")

    @property
    def plugins(self):
        """
        Return loaded transformers in priority order, such that modules with a
        higher `priority` rank are called first and changes from lower ranked
        transformers are applied last.

        A plugin of `priority` 1 will override any existing context keys and
        will be the last to modify `audio_data`
        """
        return sorted(self.loaded_plugins.values(),
                      key=lambda k: k.priority, reverse=True)

    def shutdown(self):
        for module in self.plugins:
            try:
                module.shutdown()
            except:
                pass

    def feed_audio(self, chunk):
        #   print("...feeding audio", len(chunk))
        for module in self.plugins:
            module.feed_audio_chunk(chunk)

    def feed_hotword(self, chunk):
        #  print("....feeding ww", len(chunk))
        for module in self.plugins:
            module.feed_hotword_chunk(chunk)

    def feed_speech(self, chunk):
        try:
            for module in self.plugins:
                module.feed_speech_chunk(chunk)
        except Exception as e:
            LOG.exception(e)

    def transform(self, chunk):
        context = {'client_name': 'ovos_dinkum_listener',
                   'source': 'audio',  # default native audio source
                   'destination': ["skills"]}
        for module in self.plugins:
            try:
                LOG.debug(f"checking audio transformer: {module}")
                chunk = module.feed_speech_utterance(chunk)
                chunk, data = module.transform(chunk)
                LOG.debug(f"{module.name}: {data}")
                context = merge_dict(context, data)
            except Exception as e:
                LOG.exception(e)
        return chunk, context
