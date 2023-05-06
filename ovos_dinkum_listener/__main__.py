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
from ovos_config import Configuration
from ovos_utils import wait_for_exit_signal

from ovos_dinkum_listener.service import OVOSDinkumVoiceService


def main():
    """Service entry point"""
    listener = Configuration().get("listener", {})
    mic = None
    # TODO - argparse to allow pyaudio
    #from ovos_dinkum_listener.voice_loop.pyaudio_microphone import PyAudioMicrophone
    #mic = PyAudioMicrophone(sample_rate=listener.get("sample_rate", 1600),
    #                        sample_width=listener.get("sample_width", 2),
    #                        sample_channels=listener.get("sample_channels", 1),
    #                        chunk_size=listener.get("chunk_size", 4096))
    service = OVOSDinkumVoiceService(mic=mic)
    service.start()
    wait_for_exit_signal()
    service.stop()


if __name__ == "__main__":
    main()
