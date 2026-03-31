#!/usr/bin/env python3
"""
screenstudio-export — Export Screen Studio projects to MP4 without a subscription.

Supported effects:
  - Zoom animations (manual, follow-mouse, follow-click-groups)
  - Speed changes (timeScale per slice)
  - Spring physics animations (viewport + cursor)
  - Cursor rendering with hotspot alignment
  - Motion blur on viewport transitions

Usage:
  python3 screenstudio-export.py <project.screenstudio> [options]

Options:
  -o, --output FILE       Output file path (default: <project-name>.mp4)
  --fps N                 Output frame rate (default: 60)
  --width N               Output width (default: 1920)
  --height N              Output height (default: 1080)
  --deadzone N            Follow-mode deadzone in pixels (default: 160)
  --blur-subframes N      Motion blur sub-frames, 1=off (default: 7)
  --bitrate STR           Video bitrate (default: 12M)
  --no-cursor             Hide cursor overlay
  --no-motion-blur        Disable motion blur
  --software-encoder      Use libx264 instead of hardware encoder
"""

import argparse
import json
import math
import os
import subprocess
import sys
import bisect
from pathlib import Path
from PIL import Image


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def parse_args():
    p = argparse.ArgumentParser(
        prog="screenstudio-export",
        description="Export Screen Studio projects to MP4",
    )
    p.add_argument("project", type=Path, help="Path to .screenstudio project directory")
    p.add_argument("-o", "--output", type=Path, default=None, help="Output MP4 path")
    p.add_argument("--fps", type=int, default=60, help="Output FPS (default: 60)")
    p.add_argument("--width", type=int, default=1920, help="Output width (default: 1920)")
    p.add_argument("--height", type=int, default=1080, help="Output height (default: 1080)")
    p.add_argument("--deadzone", type=int, default=160, help="Follow-mode deadzone px (default: 160)")
    p.add_argument("--blur-subframes", type=int, default=7, help="Motion blur sub-frames (default: 7)")
    p.add_argument("--bitrate", type=str, default="12M", help="Video bitrate (default: 12M)")
    p.add_argument("--no-cursor", action="store_true", help="Hide cursor")
    p.add_argument("--no-motion-blur", action="store_true", help="Disable motion blur")
    p.add_argument("--software-encoder", action="store_true", help="Use libx264 instead of HW encoder")
    return p.parse_args()


# ─── Video frame reader ─────────────────────────────────────────────────────

class VideoFrameReader:
    def __init__(self, video_path, width, height):
        self.width = width
        self.height = height
        self.frame_size = width * height * 3
        self.video_path = str(video_path)
        self.process = None
        self.current_time_ms = 0.0
        self._last_frame_data = None
        self._start_decoder(0)

    def _start_decoder(self, seek_ms):
        if self.process:
            try:
                self.process.stdout.close()
                self.process.kill()
                self.process.wait()
            except Exception:
                pass
        seek_s = max(0, seek_ms / 1000.0)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{seek_s:.3f}",
            "-i", self.video_path,
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.width}x{self.height}",
            "-r", "60", "-an", "pipe:1",
        ]
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=self.frame_size * 4)
        self.current_time_ms = seek_ms
        self._last_frame_data = None

    def read_frame_at(self, target_ms):
        if target_ms < self.current_time_ms - 100 or target_ms > self.current_time_ms + 10000:
            self._start_decoder(target_ms)
        interval = 1000.0 / 60.0
        while self.current_time_ms + interval < target_ms:
            data = self.process.stdout.read(self.frame_size)
            if not data or len(data) < self.frame_size:
                break
            self.current_time_ms += interval
            self._last_frame_data = data
        data = self.process.stdout.read(self.frame_size)
        if data and len(data) == self.frame_size:
            self.current_time_ms += interval
            self._last_frame_data = data
            return data
        if self._last_frame_data:
            return self._last_frame_data
        return b'\x00' * self.frame_size

    def close(self):
        if self.process:
            try:
                self.process.stdout.close()
                self.process.kill()
                self.process.wait()
            except Exception:
                pass


# ─── Project loader ──────────────────────────────────────────────────────────

class ScreenStudioProject:
    def __init__(self, project_dir, args):
        self.project_dir = Path(project_dir)
        self.recording_dir = self.project_dir / "recording"
        self.args = args

        # Validate
        if not self.project_dir.exists():
            sys.exit(f"Error: project not found: {self.project_dir}")
        if not (self.project_dir / "project.json").exists():
            sys.exit(f"Error: project.json not found in {self.project_dir}")
        if not self.recording_dir.exists():
            sys.exit(f"Error: recording directory not found in {self.project_dir}")

        # Load data
        project_data = load_json(self.project_dir / "project.json")
        self.project = project_data["json"]
        self.metadata = load_json(self.recording_dir / "metadata.json")
        self.config = self.project["config"]

        # Scene & effects
        self.scene = self.project["scenes"][0]
        self.zoom_ranges = self.scene.get("zoomRanges", [])
        self.slices = self.scene.get("slices", [])

        self.motion_blur_amount = self.config.get("motionBlurAmount", 1)

        # Source resolution from first display session
        self._load_sessions()
        self._load_mouse_data()
        self._load_cursors()
        self._build_timeline()

    def _load_sessions(self):
        """Load session info and find video files."""
        display_recorders = [
            r for r in self.metadata["recorders"] if r["type"] == "display"
        ]
        if not display_recorders:
            sys.exit("Error: no display recorder found in metadata")

        self.display_recorder = display_recorders[0]
        self.sessions = self.display_recorder["sessions"]
        self.num_sessions = len(self.sessions)

        # Source resolution from first session
        bounds = self.sessions[0]["bounds"]
        self.source_width = bounds["width"]
        self.source_height = bounds["height"]

        # Session timing from metadata top-level sessions
        meta_sessions = self.metadata["sessions"]
        self.session_infos = []
        cumulative_source_ms = 0.0
        for i, ms in enumerate(meta_sessions):
            info = {
                "index": i,
                "processTimeStartMs": ms["processTimeStartMs"],
                "durationMs": ms["durationMs"],
                "sourceStartMs": cumulative_source_ms,
            }
            cumulative_source_ms += ms["durationMs"]
            self.session_infos.append(info)
        self.total_source_duration = cumulative_source_ms

        # Video file paths
        self.video_paths = []
        for s in self.sessions:
            vpath = self.recording_dir / s["outputFilename"]
            if not vpath.exists():
                sys.exit(f"Error: video file not found: {vpath}")
            self.video_paths.append(vpath)

        print(f"Project: {self.project.get('name', 'Untitled')}")
        print(f"Source: {self.source_width}x{self.source_height}, "
              f"{self.num_sessions} session(s), {self.total_source_duration/1000:.1f}s total")

    def _load_mouse_data(self):
        """Load mouse movements and clicks from all sessions."""
        input_recorders = [r for r in self.metadata["recorders"] if r["type"] == "input"]
        self.mouse_moves = []
        self.mouse_clicks = []

        if not input_recorders:
            return

        input_rec = input_recorders[0]
        for i, sess in enumerate(input_rec.get("sessions", [])):
            si = self.session_infos[i] if i < len(self.session_infos) else None
            if not si:
                continue

            process_start = si["processTimeStartMs"]
            source_offset = si["sourceStartMs"]

            # Mouse moves
            moves_file = sess.get("mouseMovesFilename")
            if moves_file and (self.recording_dir / moves_file).exists():
                data = load_json(self.recording_dir / moves_file)
                for evt in data:
                    evt["sourceTimeMs"] = evt["processTimeMs"] - process_start + source_offset
                    self.mouse_moves.append(evt)

            # Mouse clicks
            clicks_file = sess.get("mouseClicksFilename")
            if clicks_file and (self.recording_dir / clicks_file).exists():
                data = load_json(self.recording_dir / clicks_file)
                for evt in data:
                    evt["sourceTimeMs"] = evt["processTimeMs"] - process_start + source_offset
                    if evt["type"] == "mouseDown":
                        self.mouse_clicks.append(evt)

        self.mouse_moves.sort(key=lambda e: e["sourceTimeMs"])
        self.mouse_clicks.sort(key=lambda e: e["sourceTimeMs"])
        self.mouse_move_times = [e["sourceTimeMs"] for e in self.mouse_moves]
        self.mouse_click_times = [e["sourceTimeMs"] for e in self.mouse_clicks]

        print(f"Loaded {len(self.mouse_moves)} mouse moves, {len(self.mouse_clicks)} clicks")

    def _load_cursors(self):
        """Load cursor images and hotspots."""
        self.cursor_images = {}
        self.cursor_hotspots = {}

        if self.args.no_cursor:
            return

        cursors_path = self.recording_dir / "cursors.json"
        if not cursors_path.exists():
            return

        cursors_meta = load_json(cursors_path)
        size_mult = self.config.get("cursorSize", 1.5)

        # Scale cursor for output resolution
        output_scale = self.args.width / self.source_width
        effective_scale = size_mult * output_scale

        for cm in cursors_meta:
            cid = cm["id"]
            img_path = self.recording_dir / "cursors" / f"{cid}.png"
            if not img_path.exists():
                continue
            img = Image.open(img_path).convert("RGBA")
            new_w = max(1, int(cm["standardSize"]["width"] * effective_scale))
            new_h = max(1, int(cm["standardSize"]["height"] * effective_scale))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            self.cursor_images[cid] = img
            self.cursor_hotspots[cid] = (
                cm["hotSpot"]["x"] * effective_scale,
                cm["hotSpot"]["y"] * effective_scale,
            )

    def _build_timeline(self):
        """Build output timeline from slices."""
        self.slice_timeline = []
        t = 0.0
        for s in self.slices:
            src_dur = s["sourceEndMs"] - s["sourceStartMs"]
            out_dur = src_dur * s["timeScale"]
            self.slice_timeline.append({
                **s,
                "outputStartMs": t,
                "outputEndMs": t + out_dur,
            })
            t += out_dur
        self.total_output_ms = t
        self.total_output_frames = int(t / 1000.0 * self.args.fps) + 1
        self.slice_output_starts = [s["outputStartMs"] for s in self.slice_timeline]
        print(f"Output: {self.args.width}x{self.args.height} @ {self.args.fps}fps, "
              f"{self.total_output_ms/1000:.1f}s, {self.total_output_frames} frames")

    # ─── Time mapping ────────────────────────────────────────────────────────

    def output_to_source_time(self, t_out_ms):
        idx = bisect.bisect_right(self.slice_output_starts, t_out_ms) - 1
        idx = max(0, idx)
        s = self.slice_timeline[idx]
        t_out_ms = max(s["outputStartMs"], min(t_out_ms, s["outputEndMs"]))
        elapsed = t_out_ms - s["outputStartMs"]
        return s["sourceStartMs"] + (elapsed / s["timeScale"] if s["timeScale"] > 0 else 0)

    def source_time_to_session(self, source_ms):
        for i in range(self.num_sessions - 1, -1, -1):
            if source_ms >= self.session_infos[i]["sourceStartMs"]:
                return i, source_ms - self.session_infos[i]["sourceStartMs"]
        return 0, source_ms

    # ─── Mouse interpolation ─────────────────────────────────────────────────

    def get_mouse_pos(self, source_ms):
        if not self.mouse_moves:
            return self.source_width / 2, self.source_height / 2, "arrow"
        idx = bisect.bisect_right(self.mouse_move_times, source_ms) - 1
        if idx < 0:
            m = self.mouse_moves[0]
            return m["x"], m["y"], m.get("cursorId", "arrow")
        if idx >= len(self.mouse_moves) - 1:
            m = self.mouse_moves[-1]
            return m["x"], m["y"], m.get("cursorId", "arrow")
        m0, m1 = self.mouse_moves[idx], self.mouse_moves[idx + 1]
        dt = m1["sourceTimeMs"] - m0["sourceTimeMs"]
        t = max(0, min(1, (source_ms - m0["sourceTimeMs"]) / dt)) if dt > 0 else 0
        return (
            m0["x"] + (m1["x"] - m0["x"]) * t,
            m0["y"] + (m1["y"] - m0["y"]) * t,
            m0.get("cursorId", "arrow"),
        )

    def get_last_click_pos(self, source_ms):
        idx = bisect.bisect_right(self.mouse_click_times, source_ms) - 1
        if idx < 0:
            return None
        return self.mouse_clicks[idx]["x"], self.mouse_clicks[idx]["y"]

    # ─── Zoom target ─────────────────────────────────────────────────────────

    def get_zoom_target_viewport(self, source_ms):
        SW, SH = self.source_width, self.source_height
        deadzone = self.args.deadzone

        for zr in self.zoom_ranges:
            if zr.get("isDisabled", False):
                continue
            if not (zr["startTime"] <= source_ms <= zr["endTime"]):
                continue

            zoom = zr["zoom"]
            vp_w = SW / zoom
            vp_h = SH / zoom
            ztype = zr["type"]

            if ztype == "manual":
                tx = zr["manualTargetPoint"]["x"]
                ty = zr["manualTargetPoint"]["y"]
                cx = tx * (SW - vp_w) + vp_w / 2
                cy = ty * (SH - vp_h) + vp_h / 2

            elif ztype == "follow-mouse":
                mx, my, _ = self.get_mouse_pos(source_ms)
                dx = mx - self._follow_target[0]
                dy = my - self._follow_target[1]
                if math.sqrt(dx * dx + dy * dy) > deadzone:
                    self._follow_target = [mx, my]
                cx, cy = self._follow_target

            elif ztype == "follow-click-groups":
                cp = self.get_last_click_pos(source_ms)
                if cp:
                    dx = cp[0] - self._follow_target[0]
                    dy = cp[1] - self._follow_target[1]
                    if math.sqrt(dx * dx + dy * dy) > deadzone:
                        self._follow_target = [cp[0], cp[1]]
                cx, cy = self._follow_target

            else:
                cx, cy = SW / 2, SH / 2

            crop_x = max(0, min(cx - vp_w / 2, SW - vp_w))
            crop_y = max(0, min(cy - vp_h / 2, SH - vp_h))
            return crop_x, crop_y, vp_w, vp_h

        return 0.0, 0.0, float(SW), float(SH)

    # ─── Spring simulation ───────────────────────────────────────────────────

    def simulate_springs(self):
        print("Pre-simulating spring physics...")
        sp = self.config.get("screenMovementSpring", {"mass": 2.25, "stiffness": 200, "damping": 40})
        mass, stiff, damp = sp["mass"], sp["stiffness"], sp["damping"]
        dt = 1.0 / 2000.0  # 0.5ms steps

        total_steps = int(self.total_output_ms * 2) + 1
        total_ms = int(self.total_output_ms) + 1

        SW, SH = float(self.source_width), float(self.source_height)
        self.spring_vp = [(0.0, 0.0, SW, SH)] * total_ms
        self.spring_total_ms = total_ms
        self._follow_target = [self.source_width / 2, self.source_height / 2]

        px, py, pw, ph = 0.0, 0.0, SW, SH
        vx, vy, vw, vh = 0.0, 0.0, 0.0, 0.0

        for step in range(total_steps):
            out_ms = step * 0.5
            src_ms = self.output_to_source_time(out_ms)
            tx, ty, tw, th = self.get_zoom_target_viewport(src_ms)

            ax = (-stiff * (px - tx) - damp * vx) / mass
            ay = (-stiff * (py - ty) - damp * vy) / mass
            aw = (-stiff * (pw - tw) - damp * vw) / mass
            ah = (-stiff * (ph - th) - damp * vh) / mass

            vx += ax * dt; vy += ay * dt; vw += aw * dt; vh += ah * dt
            px += vx * dt; py += vy * dt; pw += vw * dt; ph += vh * dt

            ms_idx = int(out_ms)
            if ms_idx < total_ms:
                cw = max(100.0, min(pw, SW))
                ch = max(100.0, min(ph, SH))
                cx = max(0.0, min(px, SW - cw))
                cy = max(0.0, min(py, SH - ch))
                self.spring_vp[ms_idx] = (cx, cy, cw, ch)

        print("Spring simulation complete.")

    def get_viewport(self, t_out_ms):
        idx = max(0, min(int(t_out_ms), self.spring_total_ms - 1))
        return self.spring_vp[idx]

    def viewport_velocity(self, t_out_ms):
        idx = int(t_out_ms)
        if idx <= 0 or idx >= self.spring_total_ms - 1:
            return 0.0
        prev, curr = self.spring_vp[idx - 1], self.spring_vp[idx]
        frame_ms = 1000.0 / self.args.fps
        dx = (curr[0] - prev[0]) * frame_ms
        dy = (curr[1] - prev[1]) * frame_ms
        dw = (curr[2] - prev[2]) * frame_ms
        return math.sqrt(dx * dx + dy * dy) + abs(dw) * 2


# ─── Renderer ────────────────────────────────────────────────────────────────

class Renderer:
    def __init__(self, proj: ScreenStudioProject):
        self.proj = proj
        self.args = proj.args
        self.out_w = self.args.width
        self.out_h = self.args.height

        # Cursor spring state
        ms_cfg = proj.config.get("mouseMovementSpring", {"mass": 3, "stiffness": 470, "damping": 70})
        self.ms_mass = ms_cfg["mass"]
        self.ms_stiff = ms_cfg["stiffness"]
        self.ms_damp = ms_cfg["damping"]
        self._cur = {"px": 0, "py": 0, "vx": 0, "vy": 0, "t": 0, "init": False}

        try:
            import numpy
            self.np = numpy
        except ImportError:
            self.np = None

    def smooth_cursor(self, t_out_ms, raw_x, raw_y):
        c = self._cur
        if not c["init"]:
            c["px"], c["py"] = raw_x, raw_y
            c["init"] = True
            c["t"] = t_out_ms
            return raw_x, raw_y
        dt_ms = t_out_ms - c["t"]
        if dt_ms <= 0:
            return c["px"], c["py"]
        steps = min(int(dt_ms), 200)
        dt_s = (dt_ms / steps) / 1000.0 if steps > 0 else 0.001
        px, py, vx, vy = c["px"], c["py"], c["vx"], c["vy"]
        for _ in range(steps):
            ax = (-self.ms_stiff * (px - raw_x) - self.ms_damp * vx) / self.ms_mass
            ay = (-self.ms_stiff * (py - raw_y) - self.ms_damp * vy) / self.ms_mass
            vx += ax * dt_s; vy += ay * dt_s
            px += vx * dt_s; py += vy * dt_s
        c.update(px=px, py=py, vx=vx, vy=vy, t=t_out_ms)
        return px, py

    def render_viewport(self, raw_data, vp):
        SW, SH = self.proj.source_width, self.proj.source_height
        cx, cy, cw, ch = vp
        cx = max(0, cx); cy = max(0, cy)
        cw = max(100, min(cw, SW - cx))
        ch = max(100, min(ch, SH - cy))
        img = Image.frombytes("RGB", (SW, SH), raw_data)
        cropped = img.crop((int(cx), int(cy), int(cx + cw), int(cy + ch)))
        return cropped.resize((self.out_w, self.out_h), Image.LANCZOS)

    def run(self):
        proj = self.proj
        args = self.args
        fps = args.fps
        frame_ms = 1000.0 / fps
        blur_n = args.blur_subframes if not args.no_motion_blur else 1

        # Open video readers
        readers = [
            VideoFrameReader(vp, proj.source_width, proj.source_height)
            for vp in proj.video_paths
        ]

        # Output path
        output = args.output
        if output is None:
            name = proj.project.get("name", "output").replace("/", "_")
            output = proj.project_dir.parent / f"{name}.mp4"

        # Encoder
        if args.software_encoder:
            codec_args = ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]
        else:
            codec_args = ["-c:v", "h264_videotoolbox", "-b:v", args.bitrate, "-profile:v", "high"]

        enc_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.out_w}x{self.out_h}",
            "-r", str(fps), "-i", "pipe:0",
            *codec_args,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(output),
        ]
        encoder = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE,
                                   bufsize=self.out_w * self.out_h * 3 * 2)

        total = proj.total_output_frames
        print(f"Rendering {total} frames...")
        if blur_n > 1:
            print(f"Motion blur: {blur_n} sub-frames")

        last_pct = -1
        for fi in range(total):
            t_out = fi * frame_ms
            t_src = proj.output_to_source_time(t_out)
            si, vid_t = proj.source_time_to_session(t_src)

            raw = readers[si].read_frame_at(vid_t)
            vp = proj.get_viewport(t_out)

            # Motion blur
            vel = proj.viewport_velocity(t_out)
            if vel > 5.0 and blur_n > 1 and proj.motion_blur_amount > 0:
                if self.np:
                    accum = None
                    for s in range(blur_n):
                        sub_t = t_out + (s - blur_n // 2) * (frame_ms / blur_n) * 0.5
                        sub_t = max(0, min(sub_t, proj.total_output_ms))
                        sub_vp = proj.get_viewport(sub_t)
                        sub_img = self.render_viewport(raw, sub_vp)
                        arr = self.np.array(sub_img, dtype=self.np.float32)
                        accum = arr if accum is None else accum + arr
                    accum /= blur_n
                    frame = Image.fromarray(accum.astype(self.np.uint8))
                else:
                    frame = None
                    for s in range(blur_n):
                        sub_t = t_out + (s - blur_n // 2) * (frame_ms / blur_n) * 0.5
                        sub_t = max(0, min(sub_t, proj.total_output_ms))
                        sub_vp = proj.get_viewport(sub_t)
                        sub_img = self.render_viewport(raw, sub_vp)
                        frame = sub_img if frame is None else Image.blend(frame, sub_img, 1.0 / (s + 1))
            else:
                frame = self.render_viewport(raw, vp)

            # Cursor
            if not args.no_cursor and proj.cursor_images:
                raw_mx, raw_my, cid = proj.get_mouse_pos(t_src)
                smx, smy = self.smooth_cursor(t_out, raw_mx, raw_my)
                cx, cy, cw, ch = vp
                if cid in proj.cursor_images:
                    cimg = proj.cursor_images[cid]
                    hx, hy = proj.cursor_hotspots[cid]
                    sx = (smx - cx) * (self.out_w / cw)
                    sy = (smy - cy) * (self.out_h / ch)
                    px, py = int(sx - hx), int(sy - hy)
                    if -cimg.width < px < self.out_w and -cimg.height < py < self.out_h:
                        frame.paste(cimg, (px, py), cimg)

            encoder.stdin.write(frame.tobytes())

            pct = fi * 100 // total
            if pct != last_pct:
                last_pct = pct
                print(f"\r  {pct}% ({fi}/{total})", end="", flush=True)

        print("\n  Finalizing...")
        encoder.stdin.close()
        encoder.wait()
        for r in readers:
            r.close()

        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"\nDone! {output}")
        print(f"  {size_mb:.1f} MB, {proj.total_output_ms/1000:.1f}s, "
              f"{self.out_w}x{self.out_h} @ {fps}fps")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # Validate project path
    proj_path = args.project
    if not str(proj_path).endswith(".screenstudio"):
        # Maybe they passed a directory containing .screenstudio files
        candidates = list(proj_path.glob("*.screenstudio")) if proj_path.is_dir() else []
        if candidates:
            proj_path = candidates[0]
            print(f"Found project: {proj_path.name}")
        else:
            sys.exit(f"Error: {proj_path} is not a .screenstudio project")

    proj = ScreenStudioProject(proj_path, args)
    proj.simulate_springs()

    renderer = Renderer(proj)
    renderer.run()


if __name__ == "__main__":
    main()
