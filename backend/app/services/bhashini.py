"""Bhashini ULCA client for ASR + English translation + TTS.

Bhashini is a two-call API:
1. POST the pipeline config endpoint to discover the callback URL, auth header,
   and service IDs for the requested task/language pair.
2. POST the compute endpoint with the audio or text.

Config responses are cached because they change rarely and are the slow part of
the voice round trip.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class BhashiniError(RuntimeError):
    pass


@dataclass
class _Pipeline:
    callback_url: str
    auth_name: str
    auth_value: str
    configs: dict[str, list[dict[str, Any]]]
    fetched_at: float


def _http_detail(response: httpx.Response) -> str:
    """Turn an HTTP failure into something a planner can act on."""

    detail = response.text[:300].strip()
    if 300 <= response.status_code < 400:
        location = response.headers.get("location", "")
        return (
            f"HTTP {response.status_code} redirect"
            + (f" to {location}" if location else "")
            + " (a proxy or network filter may be blocking Bhashini)"
        )
    if detail:
        return f"HTTP {response.status_code}: {detail}"
    return f"HTTP {response.status_code}"


class BhashiniClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._cache: dict[tuple[str, ...], _Pipeline] = {}
        self._lock = asyncio.Lock()
        self._cache_ttl_s = 3600

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.settings.bhashini_timeout_s,
            transport=self._transport,
        )

    def _require_config(self) -> None:
        if not self.settings.bhashini_configured:
            raise BhashiniError(
                "Bhashini is not configured. Set BHASHINI_USER_ID and BHASHINI_API_KEY."
            )

    async def _get_pipeline(self, tasks: tuple[str, ...]) -> _Pipeline:
        self._require_config()
        cached = self._cache.get(tasks)
        if cached and time.time() - cached.fetched_at < self._cache_ttl_s:
            return cached

        async with self._lock:
            cached = self._cache.get(tasks)
            if cached and time.time() - cached.fetched_at < self._cache_ttl_s:
                return cached

            payload = {
                "pipelineTasks": [{"taskType": task} for task in tasks],
                "pipelineRequestConfig": {"pipelineId": self.settings.bhashini_pipeline_id},
            }
            headers = {
                "userID": self.settings.bhashini_user_id or "",
                "ulcaApiKey": self.settings.bhashini_api_key or "",
            }
            url = (
                f"{self.settings.bhashini_base_url.rstrip('/')}"
                "/ulca/apis/v0/model/getModelsPipeline"
            )
            try:
                async with self._client() as client:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    body = response.json()
            except httpx.HTTPStatusError as exc:
                raise BhashiniError(
                    f"Bhashini pipeline config failed: {_http_detail(exc.response)}"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise BhashiniError(f"Bhashini pipeline config failed: {exc}") from exc

            endpoint = body.get("pipelineInferenceAPIEndPoint") or {}
            api_key = endpoint.get("inferenceApiKey") or {}
            configs: dict[str, list[dict[str, Any]]] = {}
            for item in body.get("pipelineResponseConfig") or []:
                configs[str(item.get("taskType"))] = list(item.get("config") or [])

            pipeline = _Pipeline(
                callback_url=str(endpoint.get("callbackUrl") or ""),
                auth_name=str(api_key.get("name") or "Authorization"),
                auth_value=str(api_key.get("value") or ""),
                configs=configs,
                fetched_at=time.time(),
            )
            if not pipeline.callback_url or not pipeline.auth_value:
                raise BhashiniError(
                    "Bhashini config response did not include an inference endpoint."
                )
            self._cache[tasks] = pipeline
            return pipeline

    @staticmethod
    def _service_id(
        pipeline: _Pipeline,
        task: str,
        source: str,
        target: str | None = None,
    ) -> str:
        configs = pipeline.configs.get(task) or []
        for config in configs:
            language = config.get("language") or {}
            if language.get("sourceLanguage") != source:
                continue
            if task == "translation" and language.get("targetLanguage") != target:
                continue
            service_id = config.get("serviceId")
            if service_id:
                return str(service_id)
        pair = f"{source}->{target}" if target else source
        raise BhashiniError(f"Bhashini pipeline does not support {task} for {pair}.")

    async def _compute(
        self,
        pipeline: _Pipeline,
        pipeline_tasks: list[dict[str, Any]],
        input_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        body = {"pipelineTasks": pipeline_tasks, "inputData": input_data}
        headers = {pipeline.auth_name: pipeline.auth_value}
        try:
            async with self._client() as client:
                response = await client.post(pipeline.callback_url, json=body, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise BhashiniError(f"Bhashini inference failed: {_http_detail(exc.response)}") from exc
        except Exception as exc:  # noqa: BLE001
            raise BhashiniError(f"Bhashini inference failed: {exc}") from exc

        outputs = payload.get("pipelineResponse")
        if not isinstance(outputs, list):
            raise BhashiniError("Bhashini returned an unexpected inference payload.")
        return outputs

    @staticmethod
    def _output_text(outputs: list[dict[str, Any]], task: str, key: str) -> str:
        for output in outputs:
            if output.get("taskType") != task:
                continue
            entries = output.get("output") or []
            if entries and entries[0].get(key):
                return str(entries[0][key])
        raise BhashiniError(f"Bhashini did not return a {task} {key}.")

    async def speech_to_english(self, audio: bytes, source_language: str) -> tuple[str, str]:
        """Return (transcript, English text)."""

        tasks: tuple[str, ...] = ("asr",) if source_language == "en" else ("asr", "translation")
        pipeline = await self._get_pipeline(tasks)
        asr_service = self._service_id(pipeline, "asr", source_language)

        pipeline_tasks: list[dict[str, Any]] = [
            {
                "taskType": "asr",
                "config": {
                    "language": {"sourceLanguage": source_language},
                    "serviceId": asr_service,
                    "audioFormat": "wav",
                    "samplingRate": 16000,
                    "preProcessors": ["vad"],
                },
            }
        ]
        if source_language != "en":
            translation_service = self._service_id(pipeline, "translation", source_language, "en")
            pipeline_tasks.append(
                {
                    "taskType": "translation",
                    "config": {
                        "language": {
                            "sourceLanguage": source_language,
                            "targetLanguage": "en",
                        },
                        "serviceId": translation_service,
                    },
                }
            )

        outputs = await self._compute(
            pipeline,
            pipeline_tasks,
            {
                "input": [{"source": None}],
                "audio": [{"audioContent": base64.b64encode(audio).decode("ascii")}],
            },
        )
        transcript = self._output_text(outputs, "asr", "source")
        if source_language == "en":
            return transcript, transcript
        english = self._output_text(outputs, "translation", "target")
        return transcript, english

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        if source_language == target_language:
            return text
        pipeline = await self._get_pipeline(("translation",))
        service_id = self._service_id(pipeline, "translation", source_language, target_language)
        outputs = await self._compute(
            pipeline,
            [
                {
                    "taskType": "translation",
                    "config": {
                        "language": {
                            "sourceLanguage": source_language,
                            "targetLanguage": target_language,
                        },
                        "serviceId": service_id,
                    },
                }
            ],
            {"input": [{"source": text}], "audio": [{"audioContent": None}]},
        )
        return self._output_text(outputs, "translation", "target")

    async def text_to_speech(self, text: str, language: str) -> tuple[str, str]:
        pipeline = await self._get_pipeline(("tts",))
        service_id = self._service_id(pipeline, "tts", language)
        outputs = await self._compute(
            pipeline,
            [
                {
                    "taskType": "tts",
                    "config": {
                        "language": {"sourceLanguage": language},
                        "serviceId": service_id,
                        "gender": "female",
                        "samplingRate": 16000,
                    },
                }
            ],
            {"input": [{"source": text}], "audio": [{"audioContent": None}]},
        )
        for output in outputs:
            if output.get("taskType") != "tts":
                continue
            audio = output.get("audio") or []
            if audio and audio[0].get("audioContent"):
                return str(audio[0]["audioContent"]), "audio/wav"
        raise BhashiniError("Bhashini did not return synthesized audio.")
