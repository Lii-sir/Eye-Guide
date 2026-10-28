from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from eyeguide.contracts import models
from eyeguide.contracts.examples import build_examples


HTTP_MODELS = (
    models.UserView, models.RegisterDevice, models.DeviceView, models.SettingsUpdate,
    models.SettingsView, models.CreateSession, models.SessionView, models.SessionPage,
    models.StopSession, models.ErrorResponse,
)
CONTRACT_DIR = Path(__file__).resolve().parents[3] / "contracts"


def build_openapi() -> dict:
    refs, definitions = models_json_schema(
        [(model, "validation") for model in HTTP_MODELS], ref_template="#/components/schemas/{model}"
    )
    operations = (
        ("get", "/api/v2/me", None, models.UserView, 200),
        ("post", "/api/v2/devices", models.RegisterDevice, models.DeviceView, 200),
        ("post", "/api/v2/devices/{device_id}/revoke", None, models.DeviceView, 200),
        ("get", "/api/v2/settings", None, models.SettingsView, 200),
        ("put", "/api/v2/settings", models.SettingsUpdate, models.SettingsView, 200),
        ("post", "/api/v2/assist-sessions", models.CreateSession, models.SessionView, 201),
        ("get", "/api/v2/assist-sessions", None, models.SessionPage, 200),
        ("get", "/api/v2/assist-sessions/{session_id}", None, models.SessionView, 200),
        ("post", "/api/v2/assist-sessions/{session_id}/stop", models.StopSession, models.SessionView, 200),
    )
    paths: dict = {}
    for method, path, request_model, response_model, status_code in operations:
        operation: dict = {
            "operationId": f"{method}_{path.removeprefix('/api/v2/').replace('/', '_').replace('{', '').replace('}', '').replace('-', '_')}",
            "responses": {
                str(status_code): {"description": "Success", "content": {
                    "application/json": {"schema": refs[(response_model, "validation")]},
                }},
            },
        }
        for error_status in (400, 401, 403, 404, 409, 429, 503, 500):
            operation["responses"][str(error_status)] = {
                "description": "Structured error", "content": {
                    "application/json": {"schema": refs[(models.ErrorResponse, "validation")]},
                },
            }
        parameters = []
        for parameter in ("device_id", "session_id"):
            if "{" + parameter + "}" in path:
                parameters.append({
                    "name": parameter, "in": "path", "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                })
        if method == "post" and path == "/api/v2/assist-sessions":
            parameters.append({
                "name": "Idempotency-Key", "in": "header", "required": True,
                "schema": {"type": "string", "format": "uuid"},
            })
        if method == "get" and path == "/api/v2/assist-sessions":
            parameters.extend([
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
                {"name": "cursor", "in": "query", "schema": {"type": "string", "maxLength": 256}},
            ])
        if parameters:
            operation["parameters"] = parameters
        if request_model is not None:
            operation["requestBody"] = {"required": True, "content": {
                "application/json": {"schema": refs[(request_model, "validation")]},
            }}
        paths.setdefault(path, {})[method] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": "EyeGuide Android/server contract", "version": "2.0.0",
                 "description": "P0 contract only. These routes are not implemented by the legacy Fake API."},
        "servers": [{"url": "https://api.example.invalid"}],
        "security": [{"accessToken": []}],
        "paths": paths,
        "components": {"schemas": definitions["$defs"], "securitySchemes": {
            "accessToken": {"type": "http", "scheme": "bearer", "description": "Validated OIDC access token; never an ID token or a URL query parameter."},
        }},
        "x-implementation-status": "contract-only",
        "x-websocket": {
            "path": "/api/v2/assist-sessions/{session_id}/stream",
            "authentication": "Authorization: Bearer access_token",
            "clientSchema": "client-message.schema.json", "serverSchema": "server-message.schema.json",
        },
    }


def build_documents() -> dict[str, dict]:
    documents = {"http.openapi.json": build_openapi(), "examples.json": build_examples()}
    for name, model in (("client-message", models.ClientMessage), ("server-message", models.ServerMessage)):
        documents[f"{name}.schema.json"] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }
    return documents


def render_document(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or check the P0 protocol contracts")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--stdout", action="store_true")
    arguments = parser.parse_args()
    documents = build_documents()
    if arguments.stdout:
        print(json.dumps(documents, ensure_ascii=True))
        return 0
    if arguments.check:
        stale = [name for name, document in documents.items()
                 if not (CONTRACT_DIR / name).exists()
                 or (CONTRACT_DIR / name).read_text(encoding="utf-8") != render_document(document)]
        if stale:
            print("Contracts need export: " + ", ".join(stale))
            return 1
        print("All four contract artifacts are current")
        return 0
    CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        (CONTRACT_DIR / name).write_text(render_document(document), encoding="utf-8")
    print("Exported four P0 contract artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
