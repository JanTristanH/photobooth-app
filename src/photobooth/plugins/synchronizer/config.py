import secrets
import sys
from platform import node
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, SecretStr, SerializationInfo, field_serializer
from pydantic_settings import SettingsConfigDict

from ... import CONFIG_PATH
from ...services.config.baseconfig import BaseConfig
from ...services.config.serializer import contextual_serializer_password

hostname = node() if node() != "" else "localhost"


class Common(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Enable plugin to sync media files globally.",
    )

    full_sync_interval: int = Field(
        default=5,
        description="Interval for full sync in minutes.",
    )

    usb_drive_change_monitor: bool = Field(
        default=True,
        description="Check if a usb drive was attached to the computer and trigger a full sync run immediately. The drive has to be configured in the remotes section, also.",
    )

    enabled_share_links: bool = Field(
        default=True,
        description="Enable Rclone, custom URL, and on-demand share link generation for QR codes. Immich share links are configured separately.",
    )

    enabled_custom_qr_url: bool = Field(
        default=False,
        description="If you have your own solution/custom setup (local hotspot, ...), enable this option and the cusotm qr url is displayed as QR code in the frontend.",
    )
    custom_qr_url: str = Field(
        default=f"http://{hostname}:8000/sharepage/#?url=http://{hostname}:8000/media/full/{{identifier}}",
        description="URL displayed as QR code to image for download. Need you to sync the files on your own or allow the user to access via hotspot. {identifier} is replaced by the actual item's id, {filename} is replaced by the actual filename on the photobooth-data, in QR code.",
    )


class RcloneConfig(BaseModel):
    model_config = SettingsConfigDict(title="Rclone Instance Settings")

    rclone_enable_logging: bool = Field(
        default=True,
        description="Enable logging to log/rclone.log.",
    )
    rclone_log_level: Literal["DEBUG", "INFO", "NOTICE", "ERROR"] = Field(
        default="NOTICE",
        description="Log verbosity.",
    )

    rclone_transfers: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Maximum number of concurrent transfers. Ensure your servers handles the amount of simultaneous connections including the connections for the checkers.",
    )

    rclone_checkers: int = Field(
        default=4,
        ge=1,
        le=8,
        description="Maximum number of concurrent checkers. Ensure your servers handles the amount of simultaneous connections including the connections for the transfers.",
    )

    enable_webui: bool = Field(
        default=True,
        description="Enable the web interface of Rclone. By default it will be accessible from the device running the app only for security reasons. Access usually via http://localhost:5573/login?url=http://localhost:5572",
    )

    # webui_allow_remote_access: bool = Field(
    #     default=False,
    #     description="If the webui is enabled, it will be bound to localhost by default, accessible only from the same device running the app. Enable remote access to connect from other network devices. WARNING: Enable only if the network is accessed only by trusted devices!",
    # )


class ShareConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Enable to generate a link displayed as QR code. You can have multiple QR codes, but it is recommended to enable only one.",
    )

    manual_public_link: str | None = Field(  # str instead HttpUrl because otherwise {}-placeholder would be encoded by pydantic
        default=None,
        description="If given, mediafiles copied to the remote must be accessible using this URL. Use {filename} to replace for the actual filename. If empty, Rclone tries to generate a public link (limited to S3 and maybe others).",
    )

    use_sharepage: bool = Field(
        default=True,
        description="Using the sharepage improves the endusers' experience when viewing mediaitems after scanning the QR code. When enabled, the sharepage URL needs to point to a public webspace, https is preferred for full functionality.",
    )
    sharepage_url: str | None = Field(
        default=None,
        description="URL used to build the links for QR codes pointing to the sharepage (if enabled above).",
    )


class OndemandShareConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Enable synchronization on this remote",
    )
    description: str = Field(
        default="default description",
        description="",
    )
    name: str = Field(
        default="",
        description="Name of the remote given during configuration including the ':' at the end. You need to setup the remote separately using the rclone web-ui at http://localhost:5573/. To sync to local folders set '/' (Linux) or 'C:\\' (Windows) and use subdir as target.",
        json_schema_extra={"list_api": "/api/admin/enumerate/rclone_remotes"},
    )
    subdir: str = Field(
        default="tmp/subdir",
        description="Subdir that is used as base to sync to. In this directory the sharepage (subdir/index.html) and mediafiles (subdir/media/) will be placed. WARNING: This directory is owned by the app - it will delete unknown files!",
    )
    apikey: str = Field(
        default_factory=lambda: secrets.token_hex(4),
        description="Random key that is used to protect the api.php endpoint against unauthorized use.",
    )
    baseurl: str = Field(
        default="http://localhost/",
        description="URL used to build the links for QR codes pointing to the sharepage (if enabled above).",
    )


class ImmichConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Upload each new original and processed media file once to an Immich album.",
    )
    server_url: HttpUrl = Field(
        default=HttpUrl("http://localhost:2283"),
        description="Immich server URL without the /api suffix.",
    )
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Immich API key with asset.upload and albumAsset.create permissions.",
    )
    album_id: UUID | None = Field(
        default=None,
        description="Target Immich album ID.",
    )
    shared_album_slug: str = Field(
        default="",
        pattern=r"^[A-Za-z0-9_-]*$",
        description="Slug of the public Immich shared link used for QR codes, for example fotobox.",
    )
    request_timeout: float = Field(
        default=30.0,
        ge=5.0,
        le=120.0,
        description="Timeout for each Immich API request in seconds.",
    )

    @field_serializer("api_key")
    def contextual_serializer(self, value, info: SerializationInfo):
        return contextual_serializer_password(value, info)


class RemoteConfig(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Enable synchronization on this remote",
    )
    description: str = Field(
        default="default description",
        description="",
    )
    name: str = Field(
        default="",
        description="Name of the remote given during configuration including the ':' at the end. You need to setup the remote separately using the rclone web-ui at http://localhost:5573/. To sync to local folders set '/' (Linux) or 'C:\\' (Windows) and use subdir as target.",
        json_schema_extra={"list_api": "/api/admin/enumerate/rclone_remotes"},
    )
    subdir: str = Field(
        default="",
        description="Subdir that is used as base to sync to. In this directory the sharepage (subdir/index.html) and mediafiles (subdir/media/) will be placed. WARNING: This directory is owned by the app - it will delete unknown files!",
    )
    copy_only_mode: bool = Field(
        default=False,
        description="Only copy files to this remote instead of full synchronization. Files are not deleted on the remote, you need to do the housekeeping on your own.",
    )

    enable_immediate_sync: bool = Field(
        default=True,
        description="Sync immediate when media is added/modified/deleted in the gallery. Enable this for QR code sharing.",
    )
    enable_regular_sync: bool = Field(
        default=True,
        description="Check media folder every X minutes and synchronize any missing files.",
    )
    enable_sharepage_sync: bool = Field(
        default=True,
        description="Copy the sharepage-file (index.html) to the remote on startup.",
    )

    shareconfig: ShareConfig


class SynchronizerConfig(BaseConfig):
    model_config = SettingsConfigDict(
        title="Synchronizer and Share-Link Generation",
        json_file=f"{CONFIG_PATH}plugin_synchronizer.json",
        env_prefix="synchronizer-",
    )

    common: Common = Common()

    rclone_config: RcloneConfig = RcloneConfig()

    immich: ImmichConfig = ImmichConfig()

    remotes: list[RemoteConfig] = [
        RemoteConfig(
            enabled=False,
            description="demo localremote",
            name="C:\\" if sys.platform == "win32" else "/",
            subdir="tmp/localsync",
            shareconfig=ShareConfig(),
        )
    ]

    ondemandshareconfig: OndemandShareConfig = OndemandShareConfig(
        enabled=False,
        description="demo localremote",
        name="C:\\" if sys.platform == "win32" else "/",
        subdir="var/www/html/ondemandshare/",
        baseurl="http://localhost/ondemandshare/",
    )
