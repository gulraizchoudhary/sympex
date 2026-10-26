from core import RecorderBase

class CameraPlaceholder(RecorderBase):
    """Explicit non-recording placeholder; does NOT claim to acquire video."""
    def __init__(self, index, bus):
        super().__init__(f'Camera {index}', bus)

    def start(self, folder, **kwargs):
        self.active = False
        self.report('WARNING', 'Camera integration is a placeholder: no video is captured')

    def stop(self):
        self.active = False
