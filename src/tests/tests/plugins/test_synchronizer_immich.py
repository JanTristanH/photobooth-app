from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import HttpUrl, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from photobooth.database.database import engine
from photobooth.database.models import ImmichAsset, Mediaitem
from photobooth.plugins.synchronizer.config import ImmichConfig
from photobooth.plugins.synchronizer.immich import ImmichUploadJob, ThreadedImmichUploader


def _config() -> ImmichConfig:
    return ImmichConfig(
        enabled=True,
        server_url=HttpUrl("https://immich.example.test"),
        api_key=SecretStr("test-key"),
        album_id=uuid4(),
        shared_album_slug="fotobox",
        request_timeout=5,
    )


def test_api_key_is_masked_in_public_config():
    config = _config()

    assert config.model_dump(context={"secrets_is_allowed": False})["api_key"] == "************"
    assert config.model_dump(context={"secrets_is_allowed": True})["api_key"] == "test-key"


def test_uploads_asset_adds_album_and_returns_processed_share_link(tmp_path: Path):
    filepath = tmp_path / "processed.jpg"
    filepath.write_bytes(b"processed-image")
    mediaitem_id = uuid4()
    asset_id = uuid4()

    with Session(engine) as session:
        session.add(
            Mediaitem(
                id=mediaitem_id,
                job_identifier=uuid4(),
                media_type="image",
                captured_original=None,
                processed=filepath,
                pipeline_config={},
                show_in_gallery=True,
            )
        )
        session.commit()

    uploader = ThreadedImmichUploader(_config(), max_retries=0)
    upload_response = MagicMock()
    upload_response.json.return_value = {"id": str(asset_id), "status": "created"}
    album_response = MagicMock()
    album_response.json.return_value = [{"id": str(asset_id), "success": True}]

    try:
        with (
            patch("photobooth.plugins.synchronizer.immich.requests.post", return_value=upload_response) as post,
            patch("photobooth.plugins.synchronizer.immich.requests.put", return_value=album_response) as put,
        ):
            uploader._upload_job(ImmichUploadJob(mediaitem_id, "processed", filepath))

        post.assert_called_once()
        assert post.call_args.args[0] == "https://immich.example.test/api/assets"
        assert post.call_args.kwargs["data"]["deviceAssetId"] == f"{mediaitem_id}-processed"
        assert post.call_args.kwargs["data"]["deviceId"] == "photobooth-app"
        put.assert_called_once()
        assert put.call_args.kwargs["json"] == {"ids": [str(asset_id)]}
        assert uploader.get_processed_share_link(mediaitem_id) == f"https://immich.example.test/s/fotobox/photos/{asset_id}"

        with Session(engine) as session:
            mapping = session.scalar(select(ImmichAsset).where(ImmichAsset.mediaitem_id == mediaitem_id))
            assert mapping is not None
            assert mapping.variant == "processed"
            assert mapping.asset_id == asset_id
    finally:
        uploader.stop()
        with Session(engine) as session:
            session.query(ImmichAsset).filter(ImmichAsset.mediaitem_id == mediaitem_id).delete()
            session.query(Mediaitem).filter(Mediaitem.id == mediaitem_id).delete()
            session.commit()


def test_duplicate_album_membership_is_accepted(tmp_path: Path):
    filepath = tmp_path / "original.jpg"
    filepath.write_bytes(b"same-image")
    mediaitem_id = uuid4()
    asset_id = uuid4()
    uploader = ThreadedImmichUploader(_config(), max_retries=0)
    upload_response = MagicMock()
    upload_response.json.return_value = {"id": str(asset_id), "status": "duplicate"}
    album_response = MagicMock()
    album_response.json.return_value = [{"id": str(asset_id), "success": False, "error": "duplicate"}]

    try:
        with (
            patch("photobooth.plugins.synchronizer.immich.requests.post", return_value=upload_response),
            patch("photobooth.plugins.synchronizer.immich.requests.put", return_value=album_response),
        ):
            uploader._upload_job(ImmichUploadJob(mediaitem_id, "original", filepath))
    finally:
        uploader.stop()
        with Session(engine) as session:
            session.query(ImmichAsset).filter(ImmichAsset.mediaitem_id == mediaitem_id).delete()
            session.commit()


def test_submit_persists_failed_upload_for_later_retry(tmp_path: Path):
    filepath = tmp_path / "pending.jpg"
    filepath.write_bytes(b"pending-image")
    mediaitem_id = uuid4()

    with Session(engine) as session:
        session.add(
            Mediaitem(
                id=mediaitem_id,
                job_identifier=uuid4(),
                media_type="image",
                captured_original=None,
                processed=filepath,
                pipeline_config={},
                show_in_gallery=True,
            )
        )
        session.commit()

    uploader = ThreadedImmichUploader(_config(), max_retries=0, pending_retry_interval=3600)
    try:
        uploader.submit(filepath)
        uploader.queue.join()

        with Session(engine) as session:
            mapping = session.scalar(select(ImmichAsset).where(ImmichAsset.mediaitem_id == mediaitem_id))
            assert mapping is not None
            assert mapping.variant == "processed"
            assert mapping.asset_id is None
            assert mapping.uploaded_at is None
    finally:
        uploader.stop()
        with Session(engine) as session:
            session.query(ImmichAsset).filter(ImmichAsset.mediaitem_id == mediaitem_id).delete()
            session.query(Mediaitem).filter(Mediaitem.id == mediaitem_id).delete()
            session.commit()


def test_restart_enqueues_only_persisted_pending_uploads(tmp_path: Path):
    pending_path = tmp_path / "pending.jpg"
    historical_path = tmp_path / "historical.jpg"
    pending_path.write_bytes(b"pending-image")
    historical_path.write_bytes(b"historical-image")
    pending_id = uuid4()
    historical_id = uuid4()

    with Session(engine) as session:
        session.add_all(
            [
                Mediaitem(
                    id=pending_id,
                    job_identifier=uuid4(),
                    media_type="image",
                    captured_original=None,
                    processed=pending_path,
                    pipeline_config={},
                    show_in_gallery=True,
                ),
                Mediaitem(
                    id=historical_id,
                    job_identifier=uuid4(),
                    media_type="image",
                    captured_original=None,
                    processed=historical_path,
                    pipeline_config={},
                    show_in_gallery=True,
                ),
                ImmichAsset(mediaitem_id=pending_id, variant="processed"),
            ]
        )
        session.commit()

    try:
        with patch("photobooth.plugins.synchronizer.immich.threading.Thread"):
            uploader = ThreadedImmichUploader(_config())

        assert uploader.queue.get_nowait() == ImmichUploadJob(pending_id, "processed", pending_path)
        assert uploader.queue.empty()
    finally:
        with Session(engine) as session:
            session.query(ImmichAsset).filter(ImmichAsset.mediaitem_id == pending_id).delete()
            session.query(Mediaitem).filter(Mediaitem.id.in_([pending_id, historical_id])).delete(synchronize_session=False)
            session.commit()


def test_share_link_without_slug_returns_immediately():
    config = _config()
    config.shared_album_slug = ""
    uploader = ThreadedImmichUploader(config, max_retries=0)

    try:
        with patch.object(uploader, "_has_mapping") as has_mapping:
            assert uploader.get_processed_share_link(uuid4(), wait_timeout=15) is None
        has_mapping.assert_not_called()
    finally:
        uploader.stop()


def test_empty_album_response_does_not_complete_mapping(tmp_path: Path):
    filepath = tmp_path / "processed.jpg"
    filepath.write_bytes(b"processed-image")
    mediaitem_id = uuid4()
    asset_id = uuid4()

    with Session(engine) as session:
        session.add(
            Mediaitem(
                id=mediaitem_id,
                job_identifier=uuid4(),
                media_type="image",
                captured_original=None,
                processed=filepath,
                pipeline_config={},
                show_in_gallery=True,
            )
        )
        session.add(ImmichAsset(mediaitem_id=mediaitem_id, variant="processed"))
        session.commit()

    uploader = ThreadedImmichUploader(_config(), max_retries=0)
    upload_response = MagicMock()
    upload_response.json.return_value = {"id": str(asset_id), "status": "created"}
    album_response = MagicMock()
    album_response.json.return_value = []

    try:
        with (
            patch("photobooth.plugins.synchronizer.immich.requests.post", return_value=upload_response),
            patch("photobooth.plugins.synchronizer.immich.requests.put", return_value=album_response),
            pytest.raises(RuntimeError, match="no album membership result"),
        ):
            uploader._upload_job(ImmichUploadJob(mediaitem_id, "processed", filepath))

        with Session(engine) as session:
            mapping = session.scalar(select(ImmichAsset).where(ImmichAsset.mediaitem_id == mediaitem_id))
            assert mapping is not None
            assert mapping.asset_id is None
    finally:
        uploader.stop()
        with Session(engine) as session:
            session.query(ImmichAsset).filter(ImmichAsset.mediaitem_id == mediaitem_id).delete()
            session.query(Mediaitem).filter(Mediaitem.id == mediaitem_id).delete()
            session.commit()
