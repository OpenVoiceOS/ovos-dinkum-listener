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
"""Opt-in upload of wake word / STT audio samples to an ovos-opendata-server.

Nothing is uploaded unless the user explicitly configures one or more
target URLs under the ``open_data`` section of mycroft.conf. There is no
default server - this is purely opt-in metrics/dataset collection to help
improve wake word and STT plugins.

https://github.com/OpenVoiceOS/ovos-opendata-server
"""
from typing import List, Optional

import requests
from ovos_config import Configuration
from ovos_utils.log import LOG
from ovos_utils.thread_utils import create_daemon


def _get_urls(key: str) -> List[str]:
    config = Configuration().get("open_data", {})
    endpoints = config.get(key, [])
    if isinstance(endpoints, str):
        endpoints = [endpoints]
    return endpoints


def _headers() -> dict:
    config = Configuration().get("open_data", {})
    headers = {"User-Agent": config.get("user_agent", "ovos-metrics")}
    api_key = config.get("api_key")
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _post_multipart(url: str, wav_bytes: bytes, data: dict):
    files = {"audio": ("sample.wav", wav_bytes, "audio/wav")}
    try:
        response = requests.post(url, data=data, files=files,
                                  headers=_headers(), timeout=5)
        LOG.info(f"Uploaded open_data sample to '{url}' - "
                 f"Response: {response.status_code}")
    except Exception as e:
        LOG.warning(f"Failed to upload open_data sample to '{url}': {e}")


def _upload_wake_word_sample(wav_bytes: bytes, name: str,
                              lang: Optional[str] = None,
                              model: Optional[str] = None,
                              plugin: Optional[str] = None,
                              plugin_config: Optional[str] = None):
    urls = _get_urls("ww_urls")
    if not urls or not name:
        return
    data = {"name": name}
    if lang:
        data["lang"] = lang
    if model:
        data["model"] = model
    if plugin:
        data["plugin"] = plugin
    if plugin_config:
        data["plugin_config"] = plugin_config
    for url in urls:
        _post_multipart(url, wav_bytes, data)


def _upload_stt_sample(wav_bytes: bytes, transcript: str, lang: str,
                        model: Optional[str] = None,
                        plugin: Optional[str] = None,
                        plugin_config: Optional[str] = None):
    urls = _get_urls("stt_urls")
    if not urls or not transcript or not lang:
        return
    data = {"transcript": transcript, "lang": lang}
    if model:
        data["model"] = model
    if plugin:
        data["plugin"] = plugin
    if plugin_config:
        data["plugin_config"] = plugin_config
    for url in urls:
        _post_multipart(url, wav_bytes, data)


def upload_wake_word_sample(wav_bytes: bytes, name: str,
                             lang: Optional[str] = None,
                             model: Optional[str] = None,
                             plugin: Optional[str] = None,
                             plugin_config: Optional[str] = None):
    """If the user configured ``open_data.ww_urls``, upload a wake word
    sample (wav bytes + metadata) to each configured server, in a daemon
    thread so it never blocks the listener.

    No-op unless ``open_data.ww_urls`` is configured - exclusively opt-in.
    """
    if not _get_urls("ww_urls"):
        return
    create_daemon(_upload_wake_word_sample,
                  (wav_bytes, name, lang, model, plugin, plugin_config))


def upload_stt_sample(wav_bytes: bytes, transcript: str, lang: str,
                       model: Optional[str] = None,
                       plugin: Optional[str] = None,
                       plugin_config: Optional[str] = None):
    """If the user configured ``open_data.stt_urls``, upload an STT
    sample (wav bytes + transcript + metadata) to each configured server,
    in a daemon thread so it never blocks the listener.

    No-op unless ``open_data.stt_urls`` is configured - exclusively opt-in.
    """
    if not _get_urls("stt_urls"):
        return
    create_daemon(_upload_stt_sample,
                  (wav_bytes, transcript, lang, model, plugin, plugin_config))
