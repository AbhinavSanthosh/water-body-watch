import json

import httpx
import pytest

from app.config import Settings
from app.services.intent import extract_intent


def _transport(content: str) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": content,
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handle)


@pytest.fixture
def nvidia_settings() -> Settings:
    return Settings(
        _env_file=None,
        nvidia_api_key="test-key",
        nvidia_timeout_s=5,
    )


async def test_nvidia_json_is_validated(nvidia_settings: Settings) -> None:
    content = json.dumps(
        {
            "target_entity": "lakes",
            "location": "Mysore Road, Bangalore",
            "action": "map_and_monitor",
            "radius_km": 4,
        }
    )
    intent, source, warning = await extract_intent(
        "Map the lakes near Mysore Road",
        nvidia_settings,
        transport=_transport(content),
    )
    assert source == "nvidia"
    assert warning is None
    assert intent.location == "Mysore Road, Bangalore"
    assert intent.target_entity == "lakes"
    assert intent.radius_km == 4


async def test_bad_nvidia_output_falls_back(nvidia_settings: Settings) -> None:
    intent, source, warning = await extract_intent(
        "Map the lakes near Mysore Road, Bangalore",
        nvidia_settings,
        transport=_transport("not json"),
    )
    assert source == "fallback"
    assert warning is not None
    assert intent.location == "Mysore Road, Bangalore"
