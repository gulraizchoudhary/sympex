"""Safe, best-effort webcam property configuration for IMMSS.

OpenCV properties depend on driver and backend. An unset optional parameter
means *do not write* to the camera, rather than writing a supposed neutral zero.
"""

DEFAULTS = {
    'width': 1280, 'height': 720, 'fps': 25.0,
    'autofocus': True, 'focus': None,
    'autoexposure': True, 'exposure': None,
    'brightness': None, 'gain': None,
    'auto_white_balance': True,
}


def normalize_settings(settings=None):
    result = dict(DEFAULTS)
    if settings:
        result.update(settings)
    result['width'] = int(result['width'])
    result['height'] = int(result['height'])
    result['fps'] = float(result['fps'])
    for key in ('focus', 'exposure', 'brightness', 'gain'):
        value = result.get(key)
        result[key] = None if value is None or str(value).strip() == '' else float(value)
    for key in ('autofocus', 'autoexposure', 'auto_white_balance'):
        result[key] = bool(result[key])
    if result['width'] < 160 or result['height'] < 120 or not 1 <= result['fps'] <= 120:
        raise ValueError('Invalid camera resolution or FPS')
    return result


def apply_camera_settings(cap, cv2, settings=None, report=None):
    """Apply only requested settings; return requested, accepted and readback.

    FPS and resolution are requests, not guarantees. In particular, OpenCV
    exposure conventions vary across Windows capture backends and drivers.
    """
    settings = normalize_settings(settings)
    result = {}

    def set_property(key, prop, value):
        try:
            accepted = bool(cap.set(prop, float(value)))
            actual = cap.get(prop)
            result[key] = {'requested': value, 'accepted': accepted, 'readback': actual}
            if not accepted and report:
                report(f'Camera property unsupported/rejected: {key}={value}')
        except Exception as exc:
            result[key] = {'requested': value, 'accepted': False, 'error': str(exc)}
            if report:
                report(f'Camera property {key} failed: {exc}')

    for key, prop in (
        ('width', cv2.CAP_PROP_FRAME_WIDTH),
        ('height', cv2.CAP_PROP_FRAME_HEIGHT),
        ('fps', cv2.CAP_PROP_FPS),
    ):
        set_property(key, prop, settings[key])

    if hasattr(cv2, 'CAP_PROP_AUTOFOCUS'):
        set_property('autofocus', cv2.CAP_PROP_AUTOFOCUS, int(settings['autofocus']))
    if not settings['autofocus'] and settings['focus'] is not None:
        set_property('focus', cv2.CAP_PROP_FOCUS, settings['focus'])

    # DirectShow typically expects 0.75 for auto and 0.25 for manual.
    # Avoid writing these magic values on MSMF/unknown backends.
    backend = None
    try:
        backend = int(cap.get(cv2.CAP_PROP_BACKEND)) if hasattr(cv2, 'CAP_PROP_BACKEND') else None
    except Exception:
        pass
    is_dshow = backend is not None and backend == getattr(cv2, 'CAP_DSHOW', None)
    if hasattr(cv2, 'CAP_PROP_AUTO_EXPOSURE') and is_dshow:
        set_property('autoexposure', cv2.CAP_PROP_AUTO_EXPOSURE,
                     .75 if settings['autoexposure'] else .25)
    elif not settings['autoexposure'] and report:
        report('Manual exposure mode cannot be reliably switched on this backend; verify camera properties')

    if not settings['autoexposure'] and settings['exposure'] is not None:
        set_property('exposure', cv2.CAP_PROP_EXPOSURE, settings['exposure'])
    if settings['brightness'] is not None:
        set_property('brightness', cv2.CAP_PROP_BRIGHTNESS, settings['brightness'])
    if settings['gain'] is not None:
        set_property('gain', cv2.CAP_PROP_GAIN, settings['gain'])

    if hasattr(cv2, 'CAP_PROP_AUTO_WB'):
        set_property('auto_white_balance', cv2.CAP_PROP_AUTO_WB,
                     int(settings['auto_white_balance']))
    return result
