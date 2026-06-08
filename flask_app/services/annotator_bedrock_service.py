from __future__ import annotations

import os
import json
import base64
import logging

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)


class AnnotatorBedrockService:
    """Lightweight Bedrock client dedicated to CBCT segmentation."""

    def __init__(self):
        region = (
            os.getenv("ANNOTATOR_BEDROCK_REGION")
            or os.getenv("BEDROCK_AWS_REGION")
            or os.getenv("AWS_REGION")
            or "us-west-2"
        )
        timeout = int(os.getenv("ANNOTATOR_BEDROCK_TIMEOUT", "90"))
        self.model_id = os.getenv(
            "ANNOTATOR_BEDROCK_MODEL",
            "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
        )

        config = Config(
            region_name=region,
            retries={"max_attempts": 2, "mode": "adaptive"},
            connect_timeout=30,
            read_timeout=timeout,
            max_pool_connections=2,
        )

        logger.info(
            "AnnotatorBedrockService using region=%s model=%s timeout=%ss",
            region,
            self.model_id,
            timeout,
        )
        self.client = boto3.client("bedrock-runtime", config=config)

    @staticmethod
    def _build_image_content(image_bytes: bytes) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            },
        }

    def invoke(
        self,
        content: list,
        max_tokens: int = 30000,
        system: str | None = None,
    ) -> str:
        """Invoke the underlying Bedrock model with arbitrary content payload."""
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }

        if system:
            payload["system"] = system

        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(payload)
        )
        body = response["body"].read()
        if isinstance(body, bytes):
            body = body.decode("utf-8")

        data = json.loads(body)
        returned_content = data.get("content", [])
        if not returned_content:
            raise ValueError("Bedrock returned empty content.")

        return returned_content[0].get("text", "")

    def segment_slice(self, image_bytes: bytes, prompt: str, max_tokens: int = 30000) -> str:
        """Compatibility helper for single-slice segmentation calls."""
        content = [
            {"type": "text", "text": prompt},
            self._build_image_content(image_bytes),
        ]
        return self.invoke(content, max_tokens=max_tokens)


_annotator_llm_service = AnnotatorBedrockService()


def get_annotator_bedrock_service() -> AnnotatorBedrockService:
    return _annotator_llm_service

