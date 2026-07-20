import copy
import os
import unittest
from unittest.mock import MagicMock, patch

from solution.llm import call_llm


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return copy.deepcopy(self.payload)


class CallLlmTests(unittest.TestCase):
    def tatu_session(self, payload):
        context = MagicMock()
        session = context.__enter__.return_value
        session.post.return_value = Response(payload)
        return context, session

    def test_prompt_transport_compatibility_exports(self):
        from solution.prompt import call_llm as legacy

        self.assertIs(legacy.call_llm_full, call_llm.call_llm_full)
        self.assertIs(legacy.call_llm_details, call_llm.call_llm_details)
        self.assertIs(legacy.assistant_history_message, call_llm.assistant_history_message)

    def test_generate_messages_copies_history(self):
        history = [{"role": "user", "content": "question"}]
        generated = call_llm.generate_messages(history)
        generated[0]["content"] = "changed"

        self.assertEqual(history[0]["content"], "question")
        self.assertEqual(
            call_llm.generate_messages("question"),
            [{"role": "user", "content": "question"}],
        )
        with self.assertRaises(TypeError):
            call_llm.generate_messages({"content": "question"})

    def test_gpt_oss_keeps_openrouter_contract(self):
        raw = {
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"total_tokens": 3},
            "echo": "key-openrouter",
        }
        with patch.dict(os.environ, {"OPENROUTER_KEY": "key-openrouter"}, clear=True), patch(
            "solution.llm.call_llm.requests.post", return_value=Response(raw)
        ) as post:
            result = call_llm.call_llm_full("question", "gpt-oss-120b")

        post.assert_called_once_with(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer key-openrouter",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-oss-120b",
                "messages": [{"role": "user", "content": "question"}],
            },
        )
        self.assertEqual(result["choices"][0]["message"]["content"], "answer")
        self.assertEqual(result["echo"], "<redacted>")

    def test_gpt_oss_local_payload_and_synthetic_history(self):
        raw = {
            "model": "gpt-oss-120b",
            "choices": [
                {
                    "message": {
                        "content": "answer",
                        "reasoning": "thought",
                        "native_turn": {"must": "not survive"},
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            "debug": "local-secret",
        }
        context, session = self.tatu_session(raw)
        history = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "[REASONING]old thought"},
            {"role": "assistant", "content": "[ANSWER]old answer"},
            {"role": "user", "content": "again"},
        ]
        with patch.dict(
            os.environ,
            {
                "GPT_OSS_BASE_URL": "http://127.0.0.1:8000/v1/",
                "GPT_OSS_API_KEY": "local-secret",
                "GPT_OSS_MAX_OUTPUT_TOKENS": "12345",
            },
            clear=True,
        ), patch("solution.llm.call_llm.requests.Session", return_value=context):
            result = call_llm.call_llm_full(history, "gpt-oss-120b")

        session.post.assert_called_once_with(
            "http://127.0.0.1:8000/v1/chat/completions",
            headers={
                "Authorization": "Bearer local-secret",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-oss-120b",
                "messages": history,
                "max_tokens": 12345,
                "stream": False,
            },
            timeout=900,
        )
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "answer")
        self.assertEqual(message["reasoning_content"], "thought")
        self.assertNotIn("native_turn", message)
        self.assertNotIn("reasoning_effort", session.post.call_args.kwargs["json"])
        self.assertNotIn("local-secret", repr(result))

    def test_gpt_oss_local_defaults_do_not_redact_normal_content(self):
        raw = {"choices": [{"message": {"content": "use a local variable"}}]}
        context, session = self.tatu_session(raw)
        with patch.dict(
            os.environ,
            {"GPT_OSS_BASE_URL": "http://127.0.0.1:8000/v1"},
            clear=True,
        ), patch("solution.llm.call_llm.requests.Session", return_value=context):
            result = call_llm.call_llm_full("question", "gpt-oss-120b")

        request = session.post.call_args.kwargs
        self.assertEqual(request["headers"]["Authorization"], "Bearer local")
        self.assertEqual(request["json"]["max_tokens"], 65536)
        self.assertEqual(result["choices"][0]["message"]["content"], "use a local variable")

    def test_openai_tatu_payload_normalization_and_limits(self):
        raw = {
            "model": "gpt-5.5-2026-07-01",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "answer",
                        "reasoning_content": "thought",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
            "debug": "tatu-secret",
        }
        context, session = self.tatu_session(raw)
        history = [
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "content": "ignored",
                "provider": "openai",
                "native_turn": {
                    "role": "assistant",
                    "content": "first answer",
                    "reasoning_content": "first thought",
                },
            },
            {"role": "user", "content": "again"},
        ]
        original = copy.deepcopy(history)
        env = {
            "TATU_API_KEY": "tatu-secret",
            "TATU_BASE_URL": "https://tatu.test/v1/",
            "TATU_MAX_OUTPUT_TOKENS": "999999",
            "TATU_TIMEOUT_SECONDS": "999999",
            "TATU_REASONING_EFFORT": "",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "solution.llm.call_llm.requests.Session", return_value=context
        ):
            result = call_llm.call_llm_full(history, "gpt-5.5")

        self.assertEqual(history, original)
        session.post.assert_called_once_with(
            "https://tatu.test/v1/chat/completions",
            headers={
                "Authorization": "Bearer tatu-secret",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-5.5",
                "messages": [
                    {"role": "user", "content": "first"},
                    {
                        "role": "assistant",
                        "content": "first answer",
                        "reasoning_content": "first thought",
                    },
                    {"role": "user", "content": "again"},
                ],
                "max_tokens": 65536,
                "stream": False,
            },
            timeout=3600,
        )
        session.mount.assert_not_called()
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "answer")
        self.assertEqual(message["reasoning_content"], "thought")
        self.assertEqual(message["provider"], "openai")
        self.assertEqual(message["native_turn"]["content"], "answer")
        self.assertEqual(result["usage"]["total_tokens"], 12)
        self.assertEqual(
            result["request_config"],
            {
                "max_output_tokens": 65536,
                "max_tokens_parameter": "max_tokens",
                "reasoning_effort": None,
                "reasoning_effort_requested": None,
                "temperature": None,
            },
        )
        self.assertNotIn("tatu-secret", repr(result))

    def test_gpt_sol_reasoning_effort_is_explicit_and_auditable(self):
        raw = {
            "choices": [{"message": {"role": "assistant", "content": "answer"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        }
        context, session = self.tatu_session(raw)
        with patch.dict(
            os.environ,
            {
                "TATU_API_KEY": "key",
                "TATU_BASE_URL": "https://tatu.test/v1",
                "TATU_REASONING_EFFORT": "max",
            },
        ), patch("solution.llm.call_llm.requests.Session", return_value=context):
            result = call_llm.call_llm_full("question", "gpt-5.6-sol")

        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["reasoning_effort"], "xhigh")
        self.assertEqual(payload["max_completion_tokens"], 65536)
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(
            result["request_config"],
            {
                "max_output_tokens": 65536,
                "max_tokens_parameter": "max_completion_tokens",
                "reasoning_effort": "xhigh",
                "reasoning_effort_requested": "max",
                "temperature": None,
            },
        )

    def test_openai_responses_uses_coding_deployer_and_native_history(self):
        old_output = [
            {
                "type": "reasoning",
                "id": "reasoning-old",
                "encrypted_content": "encrypted-old",
                "summary": [],
            },
            {
                "type": "message",
                "id": "message-old",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "old answer"}],
            },
        ]
        new_output = [
            {
                "type": "reasoning",
                "id": "reasoning-new",
                "encrypted_content": "encrypted-new",
                "summary": [{"type": "summary_text", "text": "new thought"}],
            },
            {
                "type": "message",
                "id": "message-new",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "new "},
                    {"type": "output_text", "text": "answer"},
                ],
            },
        ]
        raw = {
            "model": "gpt-5.6-sol",
            "status": "completed",
            "output": new_output,
            "usage": {
                "input_tokens": 17,
                "input_tokens_details": {"cached_tokens": 11},
                "output_tokens": 23,
                "output_tokens_details": {"reasoning_tokens": 19},
                "total_tokens": 40,
            },
            "debug": "tatu-secret",
        }
        context, session = self.tatu_session(raw)
        history = [
            {"role": "user", "content": "first"},
            {
                "role": "assistant",
                "content": "ignored",
                "provider": "openai-responses",
                "native_turn": {"output": old_output},
            },
            {"role": "user", "content": "again"},
        ]
        original = copy.deepcopy(history)
        with patch.dict(
            os.environ,
            {
                "TATU_API_KEY": "tatu-secret",
                "TATU_BASE_URL": "https://tatu.test/deployer/coding_tatu/v1/",
                "TATU_OPENAI_TRANSPORT": "responses",
                "TATU_DEPLOYER": "CODING_TATU",
                "TATU_REASONING_EFFORT": "xhigh",
                "TATU_TIMEOUT_SECONDS": "1200",
                "TATU_TEMPERATURE": "1.0",
            },
            clear=False,
        ), patch("solution.llm.call_llm.requests.Session", return_value=context):
            result = call_llm.call_llm_full(history, "gpt-5.6-sol")

        self.assertEqual(history, original)
        session.post.assert_called_once_with(
            "https://tatu.test/deployer/coding_tatu/v1/responses",
            headers={
                "Authorization": "Bearer tatu-secret",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-5.6-sol@CODING_TATU",
                "input": [
                    {"role": "user", "content": "first"},
                    *old_output,
                    {"role": "user", "content": "again"},
                ],
                "store": False,
                "max_output_tokens": 65536,
                "temperature": 1.0,
                "reasoning": {"effort": "xhigh"},
            },
            timeout=1200,
        )
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "new answer")
        self.assertEqual(message["reasoning_content"], "new thought")
        self.assertEqual(message["provider"], "openai-responses")
        self.assertEqual(message["native_turn"], {"output": new_output})
        self.assertEqual(
            result["usage"],
            {
                "prompt_tokens": 17,
                "completion_tokens": 23,
                "total_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 11},
                "completion_tokens_details": {"reasoning_tokens": 19},
            },
        )
        self.assertEqual(
            result["request_config"],
            {
                "transport": "responses",
                "request_model": "gpt-5.6-sol@CODING_TATU",
                "deployer": "CODING_TATU",
                "max_output_tokens": 65536,
                "reasoning_effort": "xhigh",
                "reasoning_effort_requested": "xhigh",
                "temperature": 1.0,
                "store": False,
            },
        )
        self.assertNotIn("tatu-secret", repr(result))

    def test_openai_transport_must_be_explicitly_supported(self):
        with patch.dict(
            os.environ,
            {"TATU_API_KEY": "key", "TATU_OPENAI_TRANSPORT": "response"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "unsupported TATU OpenAI transport"):
                call_llm.call_llm_full("question", "gpt-5.6-sol")

    def test_anthropic_native_history_and_normalization(self):
        blocks = [
            {"type": "thinking", "thinking": "new thought", "signature": "new-signature"},
            {"type": "text", "text": "new answer"},
        ]
        raw = {
            "model": "claude-fable-5",
            "content": blocks,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 11, "output_tokens": 13},
        }
        context, session = self.tatu_session(raw)
        old_blocks = [
            {"type": "thinking", "thinking": "old thought", "signature": "old-signature"},
            {"type": "text", "text": "old answer"},
        ]
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": "old answer",
                "provider": "anthropic",
                "native_turn": {"role": "assistant", "content": old_blocks},
            },
            {"role": "user", "content": "retry"},
        ]
        with patch.dict(os.environ, {"TATU_API_KEY": "key"}), patch(
            "solution.llm.call_llm.requests.Session", return_value=context
        ):
            result = call_llm.call_llm_full(history, "claude-fable-5")

        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["system"], "system")
        self.assertEqual(payload["messages"][1]["content"], old_blocks)
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "new answer")
        self.assertEqual(message["reasoning_content"], "new thought")
        self.assertEqual(message["native_turn"]["content"], blocks)
        self.assertEqual(
            result["usage"],
            {"prompt_tokens": 11, "completion_tokens": 13, "total_tokens": 24},
        )
        history_message = call_llm.assistant_history_message("new answer", message)
        self.assertEqual(history_message["native_turn"]["content"], blocks)
        self.assertNotIn("raw_response", history_message)

    def test_gemini_native_history_and_normalization(self):
        new_parts = [
            {"text": "private thought", "thought": True, "thoughtSignature": "new-signature"},
            {"text": "public answer"},
        ]
        raw = {
            "modelVersion": "gemini-3.1-pro-preview-001",
            "candidates": [
                {"content": {"role": "model", "parts": new_parts}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 17,
                "candidatesTokenCount": 19,
                "thoughtsTokenCount": 23,
                "totalTokenCount": 59,
            },
        }
        context, session = self.tatu_session(raw)
        old_parts = [
            {"text": "old thought", "thought": True, "thoughtSignature": "old-signature"},
            {"text": "old answer"},
        ]
        history = [
            {"role": "developer", "content": "system"},
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": "old answer",
                "provider": "gemini",
                "native_turn": {"role": "model", "parts": old_parts},
            },
            {"role": "user", "content": "retry"},
        ]
        with patch.dict(
            os.environ,
            {"TATU_API_KEY": "key", "TATU_BASE_URL": "https://tatu.test/v1beta/"},
        ), patch("solution.llm.call_llm.requests.Session", return_value=context):
            result = call_llm.call_llm_full(history, "gemini-3.1-pro-preview")

        self.assertEqual(
            session.post.call_args.args[0],
            "https://tatu.test/v1beta/models/gemini-3.1-pro-preview:generateContent",
        )
        payload = session.post.call_args.kwargs["json"]
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "system"}]})
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"],
            {"thinkingLevel": "high", "includeThoughts": True},
        )
        self.assertEqual(payload["contents"][1]["role"], "model")
        self.assertEqual(payload["contents"][1]["parts"], old_parts)
        message = result["choices"][0]["message"]
        self.assertEqual(message["content"], "public answer")
        self.assertEqual(message["reasoning_content"], "private thought")
        self.assertEqual(message["native_turn"]["parts"], new_parts)
        self.assertEqual(
            result["request_config"]["thinking_config"],
            {"thinkingLevel": "high", "includeThoughts": True},
        )
        self.assertEqual(
            result["usage"],
            {
                "prompt_tokens": 17,
                "completion_tokens": 42,
                "total_tokens": 59,
                "reasoning_tokens": 23,
            },
        )

    def test_gemini_usage_falls_back_to_candidate_plus_thinking_tokens(self):
        self.assertEqual(
            call_llm._usage(
                "gemini",
                {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 7,
                    "thoughtsTokenCount": 11,
                },
            ),
            {
                "prompt_tokens": 5,
                "completion_tokens": 18,
                "total_tokens": 23,
                "reasoning_tokens": 11,
            },
        )

    def test_details_shape_and_errors(self):
        normalized = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "answer",
                        "reasoning_content": "thought",
                    }
                }
            ],
            "usage": {"total_tokens": 2},
            "raw_response": {"id": "response"},
        }
        with patch("solution.llm.call_llm.call_llm_full", return_value=normalized):
            content, message, usage = call_llm.call_llm_details("question", "model")
        self.assertEqual(content, "answer")
        self.assertEqual(message["raw_response"], {"id": "response"})
        self.assertEqual(usage, {"total_tokens": 2})

        with self.assertRaisesRegex(ValueError, "unsupported model"):
            call_llm.call_llm_full("question", "unknown")
        with patch.dict(os.environ, {"TATU_API_KEY": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "TATU_API_KEY"):
                call_llm.call_llm_full("question", "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()
