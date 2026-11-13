import base64
import json

import httpx
import pytest

from app.config import Settings
from app.services.bhashini import BhashiniClient, BhashiniError


def _config_payload(tasks: list[str]) -> dict:
    configs = []
    if "asr" in tasks:
        configs.append(
            {
                "taskType": "asr",
                "config": [
                    {
                        "serviceId": "asr-kn",
                        "language": {"sourceLanguage": "kn"},
                    }
                ],
            }
        )
    if "translation" in tasks:
        configs.append(
            {
                "taskType": "translation",
                "config": [
                    {
                        "serviceId": "nmt-kn-en",
                        "language": {"sourceLanguage": "kn", "targetLanguage": "en"},
                    },
                    {
                        "serviceId": "nmt-en-kn",
                        "language": {"sourceLanguage": "en", "targetLanguage": "kn"},
                    },
                ],
            }
        )
    if "tts" in tasks:
        configs.append(
            {
                "taskType": "tts",
                "config": [
                    {
                        "serviceId": "tts-kn",
                        "language": {"sourceLanguage": "kn"},
                    }
                ],
            }
        )
    return {
        "pipelineResponseConfig": configs,
        "pipelineInferenceAPIEndPoint": {
            "callbackUrl": "https://bhashini.test/inference",
            "inferenceApiKey": {"name": "Authorization", "value": "test-token"},
        },
    }


def _handler(calls: list[str]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getModelsPipeline"):
            calls.append("config")
            body = json.loads(request.content)
            tasks = [item["taskType"] for item in body["pipelineTasks"]]
            return httpx.Response(200, json=_config_payload(tasks))

        calls.append("compute")
        body = json.loads(request.content)
        task_types = [item["taskType"] for item in body["pipelineTasks"]]
        responses = []
        if "asr" in task_types:
            responses.append(
                {
                    "taskType": "asr",
                    "output": [{"source": "ನಮಸ್ಕಾರ"}],
                }
            )
        if "translation" in task_types:
            responses.append(
                {
                    "taskType": "translation",
                    "output": [
                        {
                            "source": "ನಮಸ್ಕಾರ",
                            "target": "Map the lakes near Mysore Road",
                        }
                    ],
                }
            )
        if "tts" in task_types:
            audio = base64.b64encode(b"fake-wav").decode("ascii")
            responses.append(
                {
                    "taskType": "tts",
                    "audio": [{"audioContent": audio}],
                }
            )
        return httpx.Response(200, json={"pipelineResponse": responses})

    return httpx.MockTransport(handle)


@pytest.fixture
def bhashini_settings() -> Settings:
    return Settings(
        _env_file=None,
        bhashini_user_id="user",
        bhashini_api_key="key",
        bhashini_timeout_s=5,
    )


async def test_speech_to_english(bhashini_settings: Settings) -> None:
    calls: list[str] = []
    client = BhashiniClient(bhashini_settings, transport=_handler(calls))
    transcript, english = await client.speech_to_english(b"RIFF", "kn")
    assert transcript == "ನಮಸ್ಕಾರ"
    assert english == "Map the lakes near Mysore Road"


async def test_translation_and_tts(bhashini_settings: Settings) -> None:
    calls: list[str] = []
    client = BhashiniClient(bhashini_settings, transport=_handler(calls))
    translated = await client.translate("Mapped 2 lakes", "en", "kn")
    assert translated == "Map the lakes near Mysore Road"
    audio, mime = await client.text_to_speech("ನಮಸ್ಕಾರ", "kn")
    assert base64.b64decode(audio) == b"fake-wav"
    assert mime == "audio/wav"


async def test_config_is_cached(bhashini_settings: Settings) -> None:
    calls: list[str] = []
    client = BhashiniClient(bhashini_settings, transport=_handler(calls))
    await client.speech_to_english(b"RIFF", "kn")
    await client.speech_to_english(b"RIFF", "kn")
    assert calls.count("config") == 1
    assert calls.count("compute") == 2


async def test_missing_config_raises() -> None:
    settings = Settings(_env_file=None, bhashini_user_id=None, bhashini_api_key=None)
    client = BhashiniClient(settings)
    with pytest.raises(BhashiniError):
        await client.text_to_speech("hello", "en")
