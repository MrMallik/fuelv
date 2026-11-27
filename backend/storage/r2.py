from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

logger = logging.getLogger("fuelv.storage.r2")

DEFAULT_R2_KEY_PREFIX = "fvs"
_TRANSIENT_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _extract_safe_filename(raw_name: str) -> str:
    """Extract a safe single-segment filename across POSIX and Windows paths."""
    normalized = raw_name.replace("\\", "/").strip()
    leaf = PurePosixPath(normalized).name
    cleaned = _SAFE_FILENAME_RE.sub("_", leaf).strip(".")
    if not cleaned or cleaned in (".", ".."):
        raise ValueError(f"Invalid filename for R2 storage: {raw_name!r}")
    return cleaned


def _is_safe_r2_key(object_key: str, key_prefix: str) -> bool:
    """Return True if object_key stays strictly under key_prefix without traversal."""
    if not object_key or "\\" in object_key or "\x00" in object_key:
        return False
    clean_key = object_key.lstrip("/")
    parts = PurePosixPath(clean_key).parts
    if not parts or ".." in parts or "." in parts:
        return False
    return clean_key.startswith(f"{key_prefix}/") and len(clean_key) > len(key_prefix) + 1


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(secret_key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region_name)
    k_service = _sign(k_region, service_name)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


class R2Storage:
    """Cloudflare R2 S3-compatible object storage client using AWS Signature Version 4.

    Uploads fuel snapshots directly to `public-bucket` under the `fvs/` prefix
    and resolves public URLs via `https://r2.pritabrata.com/fvs/<filename>`.
    Never writes images to the local device/container disk.
    """

    def __init__(
        self,
        *,
        account_id: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        bucket_name: str | None = None,
        key_prefix: str | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self.account_id = (
            account_id if account_id is not None else os.getenv("R2_ACCOUNT_ID", "")
        ).strip()

        self.access_key_id = (
            access_key_id if access_key_id is not None else os.getenv("R2_ACCESS_KEY_ID", "")
        ).strip()

        self.secret_access_key = (
            secret_access_key if secret_access_key is not None else os.getenv("R2_SECRET_ACCESS_KEY", "")
        ).strip()

        self.bucket_name = (
            bucket_name if bucket_name is not None else os.getenv("R2_BUCKET_NAME", "")
        ).strip()

        raw_prefix = (
            key_prefix if key_prefix is not None else os.getenv("R2_KEY_PREFIX", DEFAULT_R2_KEY_PREFIX)
        ).strip()
        self.key_prefix = raw_prefix.strip("/") or DEFAULT_R2_KEY_PREFIX

        raw_base = (
            public_base_url
            if public_base_url is not None
            else os.getenv("R2_PUBLIC_BASE_URL", "")
        ).strip()
        self.public_base_url = raw_base.rstrip("/")

        self.host = f"{self.account_id}.r2.cloudflarestorage.com" if self.account_id else ""
        self.endpoint = f"https://{self.host}" if self.host else ""
        self.region = "auto"
        self.service = "s3"
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.Client(
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
            return self._client

    def close(self) -> None:
        """Close the pooled HTTP client if initialized."""
        with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                self._client.close()
                self._client = None

    @property
    def is_configured(self) -> bool:
        return bool(
            self.account_id
            and self.access_key_id
            and self.secret_access_key
            and self.bucket_name
            and self.public_base_url
        )

    def build_object_key(self, filename: str) -> str:
        clean_name = _extract_safe_filename(filename)
        return f"{self.key_prefix}/{clean_name}"

    def build_public_url(self, object_key: str) -> str:
        clean_key = object_key.lstrip("/")
        return f"{self.public_base_url}/{clean_key}"

    def _build_signed_headers(
        self,
        method: str,
        object_key: str,
        payload: bytes = b"",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        clean_key = object_key.lstrip("/")
        encoded_key = quote(clean_key, safe="/-_.~")
        canonical_uri = f"/{quote(self.bucket_name, safe='-_.~')}/{encoded_key}"
        url = f"{self.endpoint}{canonical_uri}"

        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = amz_date[:8]
        payload_hash = hashlib.sha256(payload).hexdigest()

        reserved_headers = {"host", "x-amz-content-sha256", "x-amz-date", "authorization"}
        sanitized_extra: dict[str, str] = {}
        headers_to_sign: dict[str, str] = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if extra_headers:
            for k, v in extra_headers.items():
                lower_k = k.lower().strip()
                if lower_k in reserved_headers:
                    continue
                normalized_v = " ".join(v.replace("\r", " ").replace("\n", " ").split())
                headers_to_sign[lower_k] = normalized_v
                sanitized_extra[k.strip()] = normalized_v

        sorted_header_keys = sorted(headers_to_sign.keys())
        canonical_headers = "".join(f"{k}:{headers_to_sign[k]}\n" for k in sorted_header_keys)
        signed_headers = ";".join(sorted_header_keys)

        canonical_request = "\n".join(
            [
                method.upper(),
                canonical_uri,
                "",  # canonical query string
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{self.region}/{self.service}/aws4_request"
        string_to_sign = "\n".join(
            [
                algorithm,
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )

        signing_key = _get_signature_key(self.secret_access_key, date_stamp, self.region, self.service)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        authorization = (
            f"{algorithm} Credential={self.access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        request_headers = dict(sanitized_extra)
        request_headers["x-amz-date"] = amz_date
        request_headers["x-amz-content-sha256"] = payload_hash
        request_headers["Authorization"] = authorization
        return url, request_headers

    def upload_image(
        self,
        data: bytes,
        filename: str,
        content_type: str = "image/jpeg",
        max_retries: int = 3,
    ) -> str:
        """Upload image bytes to Cloudflare R2 under `fvs/<filename>` and return the object key."""
        object_key = self.build_object_key(filename)

        if not self.is_configured:
            require_r2 = os.getenv("R2_REQUIRE_CREDENTIALS", "false").lower() in ("true", "1", "yes")
            if require_r2:
                raise RuntimeError(
                    "Cloudflare R2 credentials (R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY) are not configured."
                )
            logger.warning(
                "R2 credentials not set; returning virtual R2 object key %s without local disk write.",
                object_key,
            )
            return object_key

        extra_headers = {
            "Content-Type": content_type or "image/jpeg",
            "Cache-Control": "public, max-age=31536000, immutable",
        }

        attempts = max(1, max_retries)
        last_exc: Exception | None = None
        client = self._get_client()

        for attempt in range(1, attempts + 1):
            try:
                url, headers = self._build_signed_headers(
                    method="PUT",
                    object_key=object_key,
                    payload=data,
                    extra_headers=extra_headers,
                )
                resp = client.put(url, content=data, headers=headers, timeout=30.0)
                if resp.status_code in (200, 201, 204):
                    return object_key

                logger.error(
                    "R2 PUT %s failed with HTTP %s: %s",
                    object_key,
                    resp.status_code,
                    resp.text[:300],
                )
                err = RuntimeError(f"R2 PUT {object_key} failed with HTTP {resp.status_code}")
                if resp.status_code not in _TRANSIENT_HTTP_STATUSES:
                    raise err
                last_exc = err
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "R2 PUT %s transport error on attempt %d/%d: %s",
                    object_key,
                    attempt,
                    attempts,
                    exc,
                )

            if attempt < attempts:
                time.sleep(0.4 * attempt)

        raise RuntimeError(f"Failed to upload {object_key} to Cloudflare R2: {last_exc}") from last_exc

    def head_image(self, object_key: str) -> bool:
        """Return True if object_key exists in Cloudflare R2."""
        if not self.is_configured or not _is_safe_r2_key(object_key, self.key_prefix):
            return False
        url, headers = self._build_signed_headers(method="HEAD", object_key=object_key)
        try:
            resp = self._get_client().head(url, headers=headers, timeout=15.0)
            return resp.status_code == 200
        except Exception:
            return False

    def delete_image(self, object_key: str | None) -> bool:
        """Delete object_key from Cloudflare R2 if configured and key belongs to R2."""
        if not object_key or not self.is_configured:
            return False
        if not _is_safe_r2_key(object_key, self.key_prefix):
            return False
        clean_key = object_key.lstrip("/")
        url, headers = self._build_signed_headers(method="DELETE", object_key=clean_key)
        try:
            resp = self._get_client().delete(url, headers=headers, timeout=15.0)
            return resp.status_code in (200, 202, 204, 404)
        except Exception as exc:
            logger.warning("Failed to delete R2 object %s: %s", clean_key, exc)
            return False


def get_r2_storage() -> R2Storage:
    return R2Storage()


def resolve_image_url(path: str | None) -> str | None:
    """Resolve stored image path or R2 key (`fvs/...`) to a public URL."""
    if not path:
        return None
    clean = path.strip()
    if not clean or "\\" in clean or "\x00" in clean:
        return None
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean

    key_prefix = (os.getenv("R2_KEY_PREFIX", DEFAULT_R2_KEY_PREFIX).strip().strip("/")) or DEFAULT_R2_KEY_PREFIX
    public_base_url = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
    stripped = clean.lstrip("/")
    if _is_safe_r2_key(stripped, key_prefix):
        if public_base_url:
            return f"{public_base_url}/{stripped}"
        return f"/{stripped}"

    # Legacy `images/<filename>` local path fallback before migration is run
    leaf = PurePosixPath(clean.replace("\\", "/")).name
    if not leaf or leaf in (".", ".."):
        return None
    return f"/api/images/{leaf}"
