import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from termcolor import colored


DEFAULT_MODEL = "gpt-5.4-mini"


class AIFallback:
    """OpenAI advisory fallback for screenshots and Lyceum answers.

    This service never performs input. It only returns structured suggestions
    that deterministic actions or state-machine recovery may inspect.
    """

    FAILURE_SCHEMA = {
        "type": "object",
        "properties": {
            "state_guess": {"type": "string"},
            "visible_targets": {
                "type": "array",
                "items": {"type": "string"},
            },
            "suggested_recovery": {"type": "string"},
            "target_hints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["label", "x", "y", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["state_guess", "visible_targets", "suggested_recovery", "target_hints"],
        "additionalProperties": False,
    }

    LYCEUM_SCHEMA = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["answer", "confidence", "reason"],
        "additionalProperties": False,
    }

    def __init__(self, model=DEFAULT_MODEL):
        load_dotenv()
        api_key = os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
        self.enabled = bool(api_key)
        self.model = os.getenv("OPENAI_VISION_MODEL") or model
        self.client = OpenAI(api_key=api_key) if api_key else None

    @staticmethod
    def _safe_json_loads(text):
        try:
            return json.loads(text)
        except Exception:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
            raise

    @staticmethod
    def _image_data_url(path):
        path = Path(path)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        suffix = path.suffix.lower().lstrip(".") or "png"
        if suffix == "jpg":
            suffix = "jpeg"
        return f"data:image/{suffix};base64,{encoded}"

    def _request_json(self, instructions, user_content, schema_name, schema):
        if not self.enabled:
            print(colored("AI fallback skipped: OPENAI_KEY/OPENAI_API_KEY is not configured.", "yellow"))
            return None
        if not hasattr(self.client, "responses"):
            print(colored("AI fallback skipped: installed openai package lacks Responses API support.", "yellow"))
            return None

        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=[{"role": "user", "content": user_content}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except Exception as exc:
            print(colored(f"AI fallback request failed: {exc}", "red"))
            return None

        try:
            return self._safe_json_loads(response.output_text)
        except Exception as exc:
            print(colored(f"AI fallback returned unreadable JSON: {exc}", "red"))
            return None

    def analyze_failure(self, context, screenshot_path, state_history):
        if not screenshot_path:
            return None

        history_text = json.dumps(state_history[-10:], ensure_ascii=True, indent=2)
        user_content = [
            {
                "type": "input_text",
                "text": (
                    "Analyze this Rise of Kingdoms bot failure. Return advisory recovery only. "
                    "Do not solve captchas and do not instruct direct clicking. "
                    f"Window title: {getattr(context, 'window_title', 'Rise of Kingdoms')}\n"
                    f"Recent state history:\n{history_text}"
                ),
            },
            {
                "type": "input_image",
                "image_url": self._image_data_url(screenshot_path),
            },
        ]
        result = self._request_json(
            "You are an advisory UI recovery assistant for a deterministic game automation state machine.",
            user_content,
            "osrokbot_failure_analysis",
            self.FAILURE_SCHEMA,
        )
        if result and context:
            context.extracted["ai_recovery"] = result
            print(colored(f"AI recovery suggestion: {result.get('suggested_recovery', '')}", "cyan"))
        return result

    def answer_lyceum(self, question, options):
        if not question or len(options) != 4:
            return None

        option_text = "\n".join(f"{letter}: {value or ''}" for letter, value in zip("ABCD", options))
        user_content = [
            {
                "type": "input_text",
                "text": (
                    "Answer this Rise of Kingdoms Lyceum multiple-choice question. "
                    "Choose only from A, B, C, or D.\n\n"
                    f"Question: {question}\n{option_text}"
                ),
            }
        ]
        return self._request_json(
            "You answer Rise of Kingdoms Lyceum questions using concise factual reasoning.",
            user_content,
            "lyceum_answer",
            self.LYCEUM_SCHEMA,
        )
