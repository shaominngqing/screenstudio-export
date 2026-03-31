# screenstudio-export

Export [Screen Studio](https://www.screen.studio/) recordings to MP4 — no subscription required.

Screen Studio saves recordings as open project files (JSON + fragmented MP4). The editing UI is free, but exporting requires a paid subscription. This tool reads the project data and renders the final video with all configured effects.

## Supported Effects

- **Zoom animations** — manual target, follow-mouse, follow-click-groups
- **Speed changes** — per-segment time scaling (slow-mo to 24x fast-forward)
- **Spring physics** — smooth animated transitions for viewport and cursor movement
- **Cursor rendering** — original recorded cursors with hotspot alignment, scaled to output resolution
- **Motion blur** — multi-frame blending during viewport transitions
- **Multi-session** — handles recordings with any number of segments

## Requirements

- Python 3.9+
- [FFmpeg](https://ffmpeg.org/) (must be on PATH)
- [Pillow](https://pillow.readthedocs.io/)
- [NumPy](https://numpy.org/) (optional, improves motion blur performance)

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/screenstudio-export.git
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

Screen Studio's `.screenstudio` project directory contains:

```
MyRecording.screenstudio/
  project.json          # All editing config: zoom ranges, speed slices, springs, cursor settings
  meta.json             # Version info
  recording/
    metadata.json       # Session boundaries, recorder types
    channel-1-display-0.mp4   # Video segment (fragmented MP4)
    channel-1-display-0-*.m4s # Video fragments
    channel-1-display-1.mp4   # Additional segments (if multi-session)
    cursors.json        # Cursor metadata (hotspots, sizes)
    cursors/            # Cursor PNG images
    mousemoves-*.json   # Mouse position data
    mouseclicks-*.json  # Mouse click data
    keystrokes-*.json   # Keystroke data
```

The renderer:
1. Parses `project.json` to extract zoom ranges, speed slices, and spring parameters
2. Builds an output-time to source-time mapping from the slice definitions
3. Simulates spring physics in **output time** (so animations aren't affected by speed changes)
4. Decodes source video frames via FFmpeg subprocess pipes
5. Applies viewport crop + scale per frame based on the spring-animated state
6. Blends multiple sub-frames for motion blur during transitions
7. Composites the cursor at the correct screen position
8. Encodes the final output via FFmpeg (hardware-accelerated on macOS)

## Limitations

Effects not yet implemented:
- Background canvas (gradient, wallpaper, padding)
- Window rounded corners and shadows
- Camera (webcam) picture-in-picture overlay
- Device mockup frames (MacBook, iPhone, etc.)
- Audio mixing (system audio, microphone, background music)
- Keyboard shortcut overlay
- Click effects (ripple animations)
- Transcript/captions

These features are fully described in the project JSON format, so contributions to support them are welcome.

## License

MIT
