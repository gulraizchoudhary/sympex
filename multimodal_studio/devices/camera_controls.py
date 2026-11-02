"""Best-effort camera controls. Actual support depends on camera and OpenCV backend."""

DEFAULTS = {"width": 640, "height": 480, "fps": 25.0,
            "autofocus": True, "focus": 0.0, "autoexposure": True,
            "exposure": -5.0, "brightness": 0.0, "gain": 0.0}


def normalize_settings(settings=None):
    result = dict(DEFAULTS)
    if settings:
        result.update(settings)
    result["width"] = int(result["width"])
    result["height"] = int(result["height"])
    result["fps"] = float(result["fps"])
    for key in ("focus", "exposure", "brightness", "gain"):
        result[key] = float(result[key])
    for key in ("autofocus", "autoexposure"):
        result[key] = bool(result[key])
    if result["width"] < 160 or result["height"] < 120 or not 1 <= result["fps"] <= 120:
        raise ValueError("Invalid camera resolution or FPS")
    return result


def apply_camera_settings(cap, cv2, settings, report=None):
    """Return actual dimensions / FPS and non-guaranteed property readback.

    Do not change format dynamically during recording: apply on open only.
    """
    settings = normalize_settings(settings)
    props = [
        ("width", cv2.CAP_PROP_FRAME_WIDTH),
        ("height", cv2.CAP_PROP_FRAME_HEIGHT),
        ("fps", cv2.CAP_PROP_FPS),
        ("autofocus", cv2.CAP_PROP_AUTOFOCUS),
    ]
    if not settings["autofocus"]:
        props.append(("focus", cv2.CAP_PROP_FOCUS))
    # OpenCV backends use inconsistent auto-exposure conventions; set only
    # on explicit manual request, with backend-specific best-effort value.
    if not settings["autoexposure"]:
        props.append(("autoexposure", cv2.CAP_PROP_AUTO_EXPOSURE))
        props.append(("exposure", cv2.CAP_PROP_EXPOSURE))
    props.extend([("brightness", cv2.CAP_PROP_BRIGHTNESS),
                  ("gain", cv2.CAP_PROP_GAIN)])
    result = {}
    for key, prop in props:
        value = settings[key]
        if key == "autoexposure":
            value = 0.25  # often manual on DirectShow; backend-dependent
        try:
            accepted = bool(cap.set(prop, float(value)))
            actual = cap.get(prop)
            result[key] = {"requested": settings[key], "accepted": accepted, "readback": actual}
            if not accepted and report:
                report(f"Camera does not support setting {key}={settings[key]}")
        except Exception as exc:
            result[key] = {"requested": settings[key], "accepted": False, "error": str(exc)}
            if report:
                report(f"Could not apply camera setting {key}: {exc}")
    return result
