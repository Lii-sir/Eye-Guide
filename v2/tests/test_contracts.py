import copy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from eyeguide.contracts import models
from eyeguide.contracts.export import HTTP_MODELS, build_documents, render_document


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.documents = build_documents()
        self.examples = self.documents["examples.json"]

    def test_http_examples_match_python_and_exported_contract(self) -> None:
        http_models = {model.__name__: model for model in HTTP_MODELS}
        for example in self.examples["http_models"]:
            with self.subTest(model=example["model"]):
                http_models[example["model"]].model_validate(example["value"])
                schema = {
                    "$ref": "#/components/schemas/" + example["model"],
                    "components": self.documents["http.openapi.json"]["components"],
                }
                Draft202012Validator(schema, format_checker=FormatChecker()).validate(example["value"])

    def test_websocket_examples_match_both_consumers(self) -> None:
        for group, model, schema_name in (
            ("client_messages", models.ClientMessage, "client-message.schema.json"),
            ("server_messages", models.ServerMessage, "server-message.schema.json"),
        ):
            schema = self.documents[schema_name]
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            for message in self.examples[group]:
                with self.subTest(type=message["type"], status=message.get("status")):
                    model.model_validate(message)
                    validator.validate(message)

    def test_untrusted_ownership_and_credentials_are_not_request_fields(self) -> None:
        request = copy.deepcopy(self.examples["http_models"][5]["value"])
        for name in ("user_id", "access_token", "model_path"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                models.CreateSession.model_validate({**request, name: "client-controlled"})

    def test_version_and_timestamp_requirements(self) -> None:
        frame = copy.deepcopy(self.examples["client_messages"][0])
        for key, value in (("protocol_version", "1.0"), ("captured_elapsed_ms", -1),
                           ("captured_elapsed_ms", float("nan"))):
            with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                models.ClientMessage.model_validate({**frame, key: value})
        del frame["protocol_version"]
        with self.assertRaises(ValidationError):
            models.ClientMessage.model_validate(frame)

    def test_expired_results_cannot_request_speech(self) -> None:
        result = copy.deepcopy(self.examples["server_messages"][3])
        validator = Draft202012Validator(self.documents["server-message.schema.json"])
        for status in ("expired", "unavailable"):
            message = {**result, "status": status}
            with self.subTest(status=status):
                with self.assertRaises(ValidationError):
                    models.ServerMessage.model_validate(message)
                self.assertFalse(validator.is_valid(message))

    def test_unknown_vision_is_not_reported_as_safe(self) -> None:
        result = copy.deepcopy(self.examples["server_messages"][5])
        result["hazard_level"] = "none"
        with self.assertRaises(ValidationError):
            models.ServerMessage.model_validate(result)
        result["hazard_level"] = "unknown"
        result["blind_road"]["blocked"] = False
        with self.assertRaises(ValidationError):
            models.ServerMessage.model_validate(result)

    def test_box_distance_and_session_state_semantics(self) -> None:
        detection = copy.deepcopy(self.examples["server_messages"][3]["detections"][0])
        for updates in ({"bbox": [0.8, 0.1, 0.2, 0.9]}, {"distance_meters": 0}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                models.Detection.model_validate({**detection, **updates})
        session = copy.deepcopy(self.examples["http_models"][6]["value"])
        with self.assertRaises(ValidationError):
            models.SessionView.model_validate({**session, "state": "ended"})
        terminal = {**session, "state": "ended", "ended_at": "2026-10-28T00:00:01Z", "end_reason": "user_stop"}
        models.SessionView.model_validate(terminal)

    def test_heartbeat_and_replacement_invariants(self) -> None:
        limits = copy.deepcopy(self.examples["server_messages"][0]["limits"])
        with self.assertRaises(ValidationError):
            models.SessionLimits.model_validate({**limits, "idle_timeout_ms": limits["heartbeat_interval_ms"]})
        dropped = copy.deepcopy(self.examples["server_messages"][2])
        dropped["replacement_frame_id"] = None
        with self.assertRaises(ValidationError):
            models.ServerMessage.model_validate(dropped)

    def test_http_security_and_references_are_consistent(self) -> None:
        document = self.documents["http.openapi.json"]
        self.assertEqual(document["security"], [{"accessToken": []}])
        operation_ids = []

        def check_references(value: object) -> None:
            if isinstance(value, dict):
                if "$ref" in value:
                    target = document
                    for part in value["$ref"].removeprefix("#/").split("/"):
                        target = target[part]
                for child in value.values():
                    check_references(child)
            elif isinstance(value, list):
                for child in value:
                    check_references(child)

        check_references(document)
        for path, operations in document["paths"].items():
            for operation in operations.values():
                operation_ids.append(operation["operationId"])
                self.assertIn("401", operation["responses"], path)
                self.assertIn("403", operation["responses"], path)
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        creation = document["paths"]["/api/v2/assist-sessions"]["post"]
        self.assertTrue(any(parameter["name"] == "Idempotency-Key" and parameter["required"]
                            for parameter in creation["parameters"]))

    def test_exported_files_are_current(self) -> None:
        contract_dir = Path(__file__).resolve().parents[1] / "contracts"
        for name, document in self.documents.items():
            with self.subTest(name=name):
                content = (contract_dir / name).read_text(encoding="utf-8")
                self.assertEqual(json.loads(content), document)
                self.assertEqual(content, render_document(document))
