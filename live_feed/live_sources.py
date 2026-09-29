from __future__ import annotations
import logging

from vms_client import CameraSpec, DahuaVMSClient, VMSConfig, HikvisionEHomeClient
import live_feed.config as cfg

log = logging.getLogger(__name__)


def build_vms_config() -> VMSConfig:
    """VMSConfig from env; fall back to live_feed's legacy VMS_* fields when env unset."""
    vc = VMSConfig()                                   # reads VMS_HOST / VMS_*_PORT / DAHUA_NVR_*
    # Legacy VMS_URL ("http://host:5057") → host, only if VMS_HOST not explicitly set.
    if vc.vms_host == "10.0.0.113" and cfg.VMS_URL:    # default unchanged ⇒ no VMS_HOST given
        host = cfg.VMS_URL.split("://", 1)[-1].split(":", 1)[0].split("/", 1)[0]
        if host:
            vc.vms_host = host
    if not vc.dahua_nvr_ip:   vc.dahua_nvr_ip   = cfg.VMS_DEVICE_IP
    if not vc.dahua_nvr_user: vc.dahua_nvr_user = cfg.VMS_USERNAME
    if not vc.dahua_nvr_pass: vc.dahua_nvr_pass = cfg.VMS_PASSWORD
    if cfg.VMS_DEVICE_PORT:   vc.dahua_nvr_port = cfg.VMS_DEVICE_PORT
    return vc


def discover_dahua_channels(vms_cfg: VMSConfig) -> list[int]:
    """Login to Dahua NVR, fetch active channels, return sorted user channel numbers.

    Converts SDK channels (0-indexed) → user channels via the configured offset
    (user = sdk - offset; with offset=-1 → user = sdk + 1).
    Always logs out in a finally block.
    """
    client = DahuaVMSClient(
        base_url=vms_cfg.dahua_base_url,
        device_ip=vms_cfg.dahua_nvr_ip,
        device_port=vms_cfg.dahua_nvr_port,
        username=vms_cfg.dahua_nvr_user,
        password=vms_cfg.dahua_nvr_pass,
    )
    client.login()
    try:
        sdk_channels = client.get_active_channels()
    finally:
        client.logout()
    user_channels = sorted(ch - vms_cfg.dahua_channel_offset for ch in sdk_channels)
    log.info("Discovered Dahua channels SDK%s → user%s", sdk_channels, user_channels)
    return user_channels


def resolve_live_specs(cameras: list[dict], vms_cfg: VMSConfig) -> dict[str, CameraSpec]:
    """Map each camera dict (config.CAMERAS) → vms_client.CameraSpec.

    Hik cameras get UserID resolved at startup by matching device_ip against
    HikvisionEHomeClient.list_devices() — UserID is NOT stable across re-registration,
    so it is never hardcoded/persisted. Raises if a Hik IP is not registered in the VMS.
    """
    hik_cams = [c for c in cameras if c["type"] == "hik"]
    hik_user_ids: dict[str, int] = {}     # device_ip → UserID

    if hik_cams:
        hc = HikvisionEHomeClient(vms_cfg.hik_base_url)
        hc.start_all()
        devices = hc.list_devices()
        log.info("VMS registered devices (%d): %s", len(devices), devices)

        # VMS may return IP under different key names depending on firmware version
        _IP_KEYS = ("IP", "DeviceIP", "RegisterIP", "IPAddress", "ip")

        def _device_ip(d: dict) -> str | None:
            for k in _IP_KEYS:
                if d.get(k):
                    return d[k]
            return None

        registered = [_device_ip(d) for d in devices]
        for c in hik_cams:
            ip = c["device_ip"]
            match = next((d for d in devices if _device_ip(d) == ip), None)
            if match is None:
                raise RuntimeError(
                    f"Hik camera {c['id']!r} (IP={ip}) not registered in the VMS "
                    f"(registered IPs: {registered}, raw devices: {devices}). "
                    f"Set the camera's EHome/ISUP CMS address to the VMS server."
                )
            hik_user_ids[ip] = match["UserID"]
            log.info("Resolved cam %s (Hik, IP=%s) → UserID=%d", c["id"], ip, match["UserID"])

    specs: dict[str, CameraSpec] = {}
    for c in cameras:
        if c["type"] == "dahua":
            # USER channel — open_source applies the SDK offset; do NOT pre-apply it.
            specs[c["id"]] = CameraSpec(id=c["id"], kind="dahua", channel=c["channel"])
        elif c["type"] == "hik":
            specs[c["id"]] = CameraSpec(
                id=c["id"], kind="hik",
                user_id=hik_user_ids[c["device_ip"]], channel=1,
            )
        else:
            raise ValueError(f"camera {c['id']!r}: unknown type {c['type']!r}")
    return specs
