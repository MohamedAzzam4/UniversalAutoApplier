"""Contract test verifying the /api/openapi.json endpoint schemas.

Each live-review submit endpoint must be visible in the OpenAPI spec with the
intended request and response model references.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from universal_auto_applier.api.app import create_app
from universal_auto_applier.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8300,
        data_dir=tmp_path / "uaa_openapi_test",
        browser_headless=True,
        submit_mode="review",
        enable_real_submission=True,
    )


ENDPOINT_CHECKS: list[tuple[str, str, int, str, str | None]] = [
    # (method, path, success_status, response_model_ref, request_model_ref)
    ("post", "/api/submit/{application_id}/observe", 200, "ObserveResponse", None),
    ("get", "/api/submit/{application_id}/status", 200, "StatusResponse", None),
    (
        "post",
        "/api/submit/{application_id}/confirm-high-risk",
        200,
        "ConfirmHighRiskResponse",
        "ConfirmHighRiskRequest",
    ),
    (
        "post",
        "/api/submit/{application_id}/approve",
        200,
        "ApproveResponse",
        "ApproveRequest",
    ),
    ("post", "/api/submit/{application_id}/revoke", 200, "RevokeResponse", None),
    (
        "post",
        "/api/submit/{application_id}/submit",
        200,
        "SubmitResponse",
        "SubmitRequest",
    ),
]


class TestOpenApiSubmitEndpoints:
    def test_submit_endpoints_in_openapi(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)
        with TestClient(app) as client:
            resp = client.get("/api/openapi.json")
            assert resp.status_code == 200
            spec = resp.json()

        paths = spec.get("paths", {})
        schemas = spec.get("components", {}).get("schemas", {})

        for method, path, status_code, resp_model, req_model in ENDPOINT_CHECKS:
            assert path in paths, f"Path {path} missing from OpenAPI spec"
            path_item = paths[path]
            assert method in path_item, f"Method {method.upper()} missing for {path}"
            operation = path_item[method]

            # Check operationId or summary
            assert "operationId" in operation or "summary" in operation, (
                f"Operation missing operationId/summary for {method.upper()} {path}"
            )

            # Check response schema
            responses = operation.get("responses", {})
            assert str(status_code) in responses, (
                f"Response {status_code} missing for {method.upper()} {path}"
            )
            resp_content = responses[str(status_code)].get("content", {})
            assert "application/json" in resp_content, (
                f"Response content-type missing for {method.upper()} {path}"
            )
            resp_schema = resp_content["application/json"].get("schema", {})
            ref = resp_schema.get("$ref", "")
            assert resp_model in ref, (
                f"Expected response model '{resp_model}' for {method.upper()} {path}, "
                f"got ref: {ref}"
            )

            # Verify the response model exists in components/schemas
            schema_names = _resolve_ref_names(resp_schema, schemas)
            assert resp_model in schema_names, (
                f"Response model '{resp_model}' not found in schemas for "
                f"{method.upper()} {path}. Found: {schema_names}"
            )

            # Check request body schema if expected
            if req_model is not None:
                request_body = operation.get("requestBody", {})
                assert request_body, f"Request body missing for {method.upper()} {path}"
                req_content = request_body.get("content", {})
                assert "application/json" in req_content, (
                    f"Request body content-type missing for {method.upper()} {path}"
                )
                req_schema = req_content["application/json"].get("schema", {})
                req_ref = req_schema.get("$ref", "")
                assert req_model in req_ref, (
                    f"Expected request model '{req_model}' for "
                    f"{method.upper()} {path}, got ref: {req_ref}"
                )

                schema_names = _resolve_ref_names(req_schema, schemas)
                assert req_model in schema_names, (
                    f"Request model '{req_model}' not found in schemas for "
                    f"{method.upper()} {path}. Found: {schema_names}"
                )

    def test_submit_schemas_exist(self, tmp_path: Path) -> None:
        """All expected schema names for submit endpoints exist."""
        settings = _settings(tmp_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)
        with TestClient(app) as client:
            resp = client.get("/api/openapi.json")
            assert resp.status_code == 200
            spec = resp.json()

        schemas = spec.get("components", {}).get("schemas", {})
        required = {
            "ObserveResponse",
            "StatusResponse",
            "ConfirmHighRiskRequest",
            "ConfirmHighRiskResponse",
            "ApproveRequest",
            "ApproveResponse",
            "RevokeResponse",
            "SubmitRequest",
            "SubmitResponse",
            "LiveReviewSnapshotResponse",
            "LiveReviewField",
            "LiveReviewDocument",
            "LiveReviewSubmitControl",
        }
        missing = required - set(schemas.keys())
        assert not missing, f"Expected schemas missing from OpenAPI: {missing}"

    def test_observe_response_contains_snapshot(self, tmp_path: Path) -> None:
        """ObserveResponse must contain a snapshot field referencing LiveReviewSnapshotResponse."""
        settings = _settings(tmp_path)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        app = create_app(settings=settings)
        with TestClient(app) as client:
            resp = client.get("/api/openapi.json")
            assert resp.status_code == 200
            spec = resp.json()

        schemas = spec.get("components", {}).get("schemas", {})
        observe_resp = schemas.get("ObserveResponse", {})
        props = observe_resp.get("properties", {})
        assert "snapshot" in props, "ObserveResponse missing 'snapshot' property"
        snap_ref = props["snapshot"].get("$ref", "")
        assert "LiveReviewSnapshotResponse" in snap_ref, (
            f"ObserveResponse.snapshot should ref LiveReviewSnapshotResponse, got: {snap_ref}"
        )


def _resolve_ref_names(
    schema: dict[str, Any],
    _all_schemas: dict[str, Any],
) -> set[str]:
    """Extract all schema names from a $ref or oneOf/allOf chain."""
    names: set[str] = set()
    ref = schema.get("$ref", "")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        names.add(name)
    for key in ("oneOf", "anyOf", "allOf"):
        for item in schema.get(key, []):
            names.update(_resolve_ref_names(item, _all_schemas))
    for prop_value in schema.get("properties", {}).values():
        names.update(_resolve_ref_names(prop_value, _all_schemas))
    items = schema.get("items", {})
    if items:
        names.update(_resolve_ref_names(items, _all_schemas))
    return names
