# Windows camera troubleshooting

1. Confirm Windows Camera app displays a live RGB camera image; close it before scanning.
2. Enable Windows Settings > Privacy & security > Camera > Camera access and Let desktop apps access your camera.
3. From the application folder run `python camera_diagnostics.py`.
4. Read/share `camera_diagnostics.txt` for backend/index/format failure details.
5. A camera listed in Device Manager need not expose frames to OpenCV (especially Windows Hello IR).
6. This patch extends warmup/format fallbacks and diagnostics but cannot guarantee Windows hardware support.

Camera scanning takes longer because it tries different backends and formats. Actual cameras are not available in this environment for end-to-end testing.
