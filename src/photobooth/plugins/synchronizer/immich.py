import logging
import mimetypes
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from time import monotonic
from uuid import UUID

import requests
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...database.database import engine
from ...database.models import ImmichAsset, Mediaitem
from .config import ImmichConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImmichUploadJob:
    mediaitem_id: UUID
    variant: str
    filepath: Path


class ThreadedImmichUploader:
    """One-way worker that uploads each local media variant to Immich once."""

    def __init__(self, config: ImmichConfig, max_retries: int = 3, pending_retry_interval: float = 60):
        if not config.album_id:
            raise ValueError("Immich album_id is required when Immich synchronization is enabled")
        if not config.api_key.get_secret_value():
            raise ValueError("Immich api_key is required when Immich synchronization is enabled")

        self.config = config
        self.max_retries = max_retries
        self.pending_retry_interval = pending_retry_interval
        self.queue: Queue[ImmichUploadJob] = Queue()
        self._stop_event = threading.Event()
        self._uploaded_condition = threading.Condition()
        self._enqueue_pending_jobs()
        self._next_pending_retry = monotonic() + self.pending_retry_interval
        self._worker = threading.Thread(target=self._worker_loop, name="immich-upload-worker", daemon=True)
        self._worker.start()

    @property
    def api_url(self) -> str:
        return f"{str(self.config.server_url).rstrip('/')}/api"

    @property
    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "x-api-key": self.config.api_key.get_secret_value()}

    def stop(self):
        self._stop_event.set()
        self._worker.join(timeout=self.config.request_timeout + 1)

    def submit(self, filepath: Path):
        job = self._resolve_job(filepath)
        if job and self._register_pending_job(job):
            self.queue.put(job)

    def get_processed_share_link(self, mediaitem_id: UUID, wait_timeout: float = 0) -> str | None:
        if not self.config.shared_album_slug or not self._has_mapping(mediaitem_id, "processed"):
            return None

        asset_id = self._get_asset_id(mediaitem_id, "processed")
        if not asset_id and wait_timeout > 0:
            with self._uploaded_condition:
                self._uploaded_condition.wait_for(
                    lambda: self._get_asset_id(mediaitem_id, "processed") is not None,
                    timeout=wait_timeout,
                )
            asset_id = self._get_asset_id(mediaitem_id, "processed")

        if not asset_id:
            return None
        return f"{str(self.config.server_url).rstrip('/')}/s/{self.config.shared_album_slug}/photos/{asset_id}"

    def _register_pending_job(self, job: ImmichUploadJob) -> bool:
        with Session(engine) as session:
            mapping = session.scalar(select(ImmichAsset).where(ImmichAsset.mediaitem_id == job.mediaitem_id, ImmichAsset.variant == job.variant))
            if mapping:
                return False
            session.add(ImmichAsset(mediaitem_id=job.mediaitem_id, variant=job.variant))
            session.commit()
            return True

    def _enqueue_pending_jobs(self):
        with Session(engine) as session:
            pending = session.execute(
                select(ImmichAsset, Mediaitem).join(Mediaitem, Mediaitem.id == ImmichAsset.mediaitem_id).where(ImmichAsset.asset_id.is_(None))
            ).all()

            for mapping, item in pending:
                filepath = item.processed if mapping.variant == "processed" else item.captured_original
                if filepath is None:
                    logger.warning("Cannot retry pending Immich %s variant for %s because its file is unavailable", mapping.variant, item.id)
                    continue
                self.queue.put(ImmichUploadJob(item.id, mapping.variant, filepath))

    def _resolve_job(self, filepath: Path) -> ImmichUploadJob | None:
        with Session(engine) as session:
            item = session.scalar(select(Mediaitem).where(or_(Mediaitem.processed == filepath, Mediaitem.captured_original == filepath)))
            if not item:
                logger.warning("Cannot upload %s to Immich because no media item references it", filepath)
                return None
            variant = "processed" if item.processed == filepath else "original"
            return ImmichUploadJob(item.id, variant, filepath)

    @staticmethod
    def _get_asset_id(mediaitem_id: UUID, variant: str) -> UUID | None:
        with Session(engine) as session:
            mapping = session.scalar(select(ImmichAsset).where(ImmichAsset.mediaitem_id == mediaitem_id, ImmichAsset.variant == variant))
            return mapping.asset_id if mapping else None

    @staticmethod
    def _has_mapping(mediaitem_id: UUID, variant: str) -> bool:
        with Session(engine) as session:
            mapping = session.scalar(select(ImmichAsset.id).where(ImmichAsset.mediaitem_id == mediaitem_id, ImmichAsset.variant == variant))
            return mapping is not None

    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                job = self.queue.get(timeout=0.2)
            except Empty:
                if monotonic() >= self._next_pending_retry:
                    self._enqueue_pending_jobs()
                    self._next_pending_retry = monotonic() + self.pending_retry_interval
                continue

            try:
                for attempt in range(1, self.max_retries + 1):
                    try:
                        self._upload_job(job)
                        break
                    except Exception:
                        if attempt == self.max_retries:
                            logger.exception("Immich upload failed for %s; it remains pending for a later retry", job.filepath)
                        else:
                            logger.warning("Immich upload attempt %s failed for %s", attempt, job.filepath, exc_info=True)
                            if self._stop_event.wait(min(2**attempt, 10)):
                                break
            finally:
                self.queue.task_done()

    def _upload_job(self, job: ImmichUploadJob):
        if self._get_asset_id(job.mediaitem_id, job.variant):
            return
        if not job.filepath.is_file():
            raise FileNotFoundError(job.filepath)

        stat = job.filepath.stat()
        timestamp_created = datetime.fromtimestamp(stat.st_ctime).astimezone().isoformat()
        timestamp_modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
        upload_name = f"photobooth-{job.mediaitem_id}-{job.variant}{job.filepath.suffix.lower()}"
        content_type = mimetypes.guess_type(job.filepath.name)[0] or "application/octet-stream"

        with job.filepath.open("rb") as file_handle:
            response = requests.post(
                f"{self.api_url}/assets",
                headers=self.headers,
                data={
                    # Required by Immich v1/v2 servers and harmless on newer versions.
                    "deviceAssetId": f"{job.mediaitem_id}-{job.variant}",
                    "deviceId": "photobooth-app",
                    "fileCreatedAt": timestamp_created,
                    "fileModifiedAt": timestamp_modified,
                    "filename": upload_name,
                },
                files={"assetData": (upload_name, file_handle, content_type)},
                timeout=self.config.request_timeout,
            )
        response.raise_for_status()
        asset_id = UUID(response.json()["id"])

        album_response = requests.put(
            f"{self.api_url}/albums/{self.config.album_id}/assets",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"ids": [str(asset_id)]},
            timeout=self.config.request_timeout,
        )
        album_response.raise_for_status()
        album_result = album_response.json()
        if not isinstance(album_result, list) or not album_result:
            raise RuntimeError(f"Immich returned no album membership result for asset {asset_id}")
        if not album_result[0].get("success", False) and album_result[0].get("error") != "duplicate":
            raise RuntimeError(f"Immich rejected adding asset {asset_id} to album: {album_result[0]}")

        with Session(engine) as session:
            mapping = session.scalar(select(ImmichAsset).where(ImmichAsset.mediaitem_id == job.mediaitem_id, ImmichAsset.variant == job.variant))
            if mapping:
                mapping.asset_id = asset_id
                mapping.uploaded_at = datetime.now().astimezone()
            else:
                session.add(
                    ImmichAsset(
                        mediaitem_id=job.mediaitem_id,
                        variant=job.variant,
                        asset_id=asset_id,
                        uploaded_at=datetime.now().astimezone(),
                    )
                )
            session.commit()

        logger.info("Uploaded %s variant for %s to Immich asset %s", job.variant, job.mediaitem_id, asset_id)
        with self._uploaded_condition:
            self._uploaded_condition.notify_all()
