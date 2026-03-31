# screenstudio-export

[中文文档](README_CN.md)

Export [Screen Studio](https://www.screen.studio/) recordings to MP4 — no subscription required.

Screen Studio saves recordings as open project files (JSON + fragmented MP4). The editing UI is free, but exporting requires a paid subscription. This tool reads the project data and renders the final video with all configured effects.

## Feature Status

### Implemented

| Feature | Status | Description |
|---------|--------|-------------|
| Zoom — manual | Done | Zoom to a fixed point with configurable `manualTargetPoint` (x, y) |
| Zoom — follow-mouse | Done | Viewport follows cursor position with deadzone to reduce jitter |
| Zoom — follow-click-groups | Done | Viewport follows the most recent click position |
| Speed changes | Done | Per-slice `timeScale` from slow-mo to 24x fast-forward, with gap/cut support |
| Spring physics (viewport) | Done | Smooth zoom/pan transitions using spring simulation (mass, stiffness, damping) |
| Spring physics (cursor) | Done | Smooth cursor movement independent of raw mouse data |
| Spring in output time | Done | Animations are not affected by speed changes — transitions stay smooth during fast-forward |
| Cursor rendering | Done | Original recorded cursor images with hotspot alignment and configurable size multiplier |
| Cursor auto-scaling | Done | Cursor size adapts to output resolution |
| Motion blur | Done | Multi-frame blending during viewport transitions (configurable sub-frame count) |
| Multi-session | Done | Handles recordings with any number of video segments |
| Timeline mapping | Done | Accurate output-time to source-time mapping across slices and sessions |
| Hardware encoding | Done | macOS VideoToolbox H.264 encoder with software (libx264) fallback |

### Not Yet Implemented

| Feature | Priority | Config Fields | Notes |
|---------|----------|---------------|-------|
| **Background canvas** | High | `backgroundType`, `backgroundGradient`, `backgroundImage`, `backgroundSystemName`, `backgroundBlur`, `backgroundColor` | When `backgroundPaddingRatio > 0`, video is inset on a gradient/wallpaper/image background |
| **Background padding** | High | `backgroundPaddingRatio`, `insetPadding` (top/bottom/left/right), `insetColor`, `insetAlpha` | Controls the canvas area around the recording |
| **Window rounded corners** | High | `windowBorderRadius` | Rounds the corners of the recording viewport |
| **Window shadow** | Medium | `shadowIntensity`, `shadowAngle`, `shadowDistance`, `shadowBlur`, `shadowIsDirectional` | Drop shadow behind the recording window |
| **Camera overlay** | Medium | `hideCamera`, `mirrorCamera`, `cameraRoundness`, `cameraSize`, `cameraPosition`, `cameraPositionPoint`, `cameraScaleDuringZoom`, `cameraAspectRatio` | Picture-in-picture webcam overlay; requires camera video channel in recording data |
| **Audio — system** | Medium | `audioVolume`, `muteSystemAudio` | System audio track mixing |
| **Audio — microphone** | Medium | `muteMicrophone`, `improveMicrophoneAudio`, `microphoneInStereoMode` | Microphone track mixing with optional enhancement |
| **Audio — external device** | Low | `muteExternalDeviceAudio` | External device audio mixing |
| **Audio — background music** | Low | `backgroundAudioFileName`, `muteBackgroundAudio`, `backgroundAudioVolume` | Background music file overlay |
| **Audio — per-slice volume** | Low | slice `volume`, `systemAudioVolume`, `externalDeviceAudioVolume` | Independent volume control per time slice |
| **Click sound effects** | Low | `clickSoundEffect`, `clickSoundEffectVolume` | Audio feedback on mouse clicks |
| **Click visual effects** | Low | `clickEffect` | Ripple/highlight animation on click |
| **Keyboard shortcut overlay** | Medium | `showShortcuts`, `hiddenShortcuts`, `showShortcutsWithSingleLetters`, `shortcutsSizeRatio` | Floating UI showing pressed key combinations |
| **Transcript/captions** | Low | `showTranscript`, `transcriptSizeRatio` | Speech-to-text captions overlay |
| **Device mockup frames** | Low | `deviceFrameKey`, `enableDeviceMockup`, `adjustDeviceFrameToRecordingSize` | MacBook/iPhone/iPad frame wrapping the recording; assets are inside Screen Studio's app bundle |
| **Recording crop** | Low | `recordingCrop` (x, y, width, height normalized) | Crop a sub-region of the source recording |
| **Glide animation** | Low | zoomRange `glideDirection`, `glideSpeed` | Slow panning movement within a zoom range |
| **Instant zoom** | Low | zoomRange `hasInstantAnimation` | Skip spring animation for instant zoom cuts |
| **Always keep zoomed** | Low | `alwaysKeepZoomedIn` | Prevent zoom-out between zoom ranges |
| **Cursor auto-hide** | Low | `hideNotMovingCursorAfterMs` | Hide cursor after idle timeout |
| **Cursor shake removal** | Low | `removeCurshorShakeTreshold` | Filter out small jittery cursor movements |
| **Cursor loop** | Low | `loopCursorPositionBeforeEndMs` | Loop cursor position near the end of recording |
| **Per-slice cursor hide** | Low | slice `hideCursor` | Hide cursor for specific time slices |
| **Per-slice smooth toggle** | Low | slice `disableSmoothMouseMovement` | Disable cursor spring for specific slices |
| **Layout system** | Low | `defaultLayout`, scene `layouts` | Multi-source layout arrangements |
| **Masks** | Low | scene `masks` | Region masking/blurring |
| **Snap to edges** | Low | zoomRange `snapToEdgesRatio` | Snap viewport to screen edges when close |
| **Output aspect ratio** | Low | `defaultOutputAspectRatio` | Force a specific aspect ratio with letterboxing |

### Project File Format Reference

```
MyRecording.screenstudio/
  project.json            # Editing config: zoom ranges, speed slices, springs, cursor, background, etc.
  meta.json               # Screen Studio version info
  recording/
    metadata.json          # Session boundaries, recorder types, display bounds
    channel-1-display-0.mp4     # Video init segment (fragmented MP4, H.264)
    channel-1-display-0-*.m4s   # Video media segments
    channel-1-display-0.m3u8    # HLS playlist (not used by this tool)
    channel-1-display-1.mp4     # Additional session video (if multi-session)
    cursors.json           # Cursor type metadata: id, hotSpot {x,y}, standardSize {w,h}
    cursors/               # Cursor PNG images (arrow.png, iBeam.png, pointingHand.png, ...)
    mousemoves-0.json      # Mouse position events: {x, y, processTimeMs, cursorId}
    mousemoves-1.json      # Mouse data for session 1 (if multi-session)
    mouseclicks-0.json     # Click events: {x, y, processTimeMs, button, type: mouseDown/mouseUp}
    keystrokes-0.json      # Key events: {character, activeModifiers, processTimeMs}
    metadata-raw.json      # Raw recording metadata
    polyrecorder.log       # Recording engine log
```

## Requirements

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) (must be on PATH)
- [Pillow](https://pillow.readthedocs.io/)
- [NumPy](https://numpy.org/) (optional, improves motion blur performance)

## Installation

```bash
git clone https://github.com/shaominngqing/screenstudio-export.git
cd screenstudio-export
pip install -r requirements.txt
```

## Usage

```bash
# Basic — exports to <project-name>.mp4 in the same directory
python screenstudio-export.py "My Recording.screenstudio"

# Specify output path and resolution
python screenstudio-export.py "My Recording.screenstudio" -o output.mp4 --width 1920 --height 1080

# 4K 60fps export
python screenstudio-export.py "My Recording.screenstudio" -o 4k.mp4 --width 3840 --height 2160 --fps 60 --bitrate 20M

# Fast render (no motion blur)
python screenstudio-export.py "My Recording.screenstudio" --no-motion-blur

# Linux or no hardware encoder
python screenstudio-export.py "My Recording.screenstudio" --software-encoder
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `-o, --output` | `<name>.mp4` | Output file path |
| `--fps` | `60` | Output frame rate |
| `--width` | `1920` | Output width in pixels |
| `--height` | `1080` | Output height in pixels |
| `--deadzone` | `160` | Follow-mode deadzone in pixels (reduces jitter) |
| `--blur-subframes` | `7` | Motion blur sub-frames (1 = off) |
| `--bitrate` | `12M` | Video bitrate |
| `--no-cursor` | — | Hide cursor overlay |
| `--no-motion-blur` | — | Disable motion blur |
| `--software-encoder` | — | Use libx264 instead of macOS VideoToolbox |

## How It Works

1. Parses `project.json` to extract zoom ranges, speed slices, and spring parameters
2. Builds an output-time to source-time mapping from the slice definitions
3. Simulates spring physics in **output time** (so animations aren't affected by speed changes)
4. Decodes source video frames via FFmpeg subprocess pipes
5. Applies viewport crop + scale per frame based on the spring-animated state
6. Blends multiple sub-frames for motion blur during transitions
7. Composites the cursor at the correct screen position
8. Encodes the final output via FFmpeg (hardware-accelerated on macOS)

## Disclaimer

This project is an independent tool that reads openly structured project files (plain JSON and standard MP4 video). It does not contain, reverse-engineer, or redistribute any Screen Studio source code, binaries, or proprietary assets.

Screen Studio is an excellent product — if you use it regularly, please consider [supporting the developers](https://www.screen.studio/) with a subscription. This tool is intended for personal and educational use.

## Contributing

The Screen Studio project format is fully open (plain JSON + standard video files). All config fields for unimplemented features are documented in the feature table above. PRs welcome!

## License

MIT
