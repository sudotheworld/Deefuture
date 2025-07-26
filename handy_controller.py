import sys
import requests

class HandyController:
    def __init__(self, handy_key="", base_url="https://www.handyfeeling.com/api/handy/v2/"):
        self.handy_key = handy_key
        self.base_url = base_url
        self.last_stroke_speed = 0
        self.last_depth_pos = 50
        self.last_relative_speed = 50
        self.min_user_speed = 10
        self.max_user_speed = 80
        self.max_handy_depth = 100
        self.min_handy_depth = 0
        self.FULL_TRAVEL_MM = 110.0

    def set_api_key(self, key):
        self.handy_key = key

    def update_settings(self, min_speed, max_speed, min_depth, max_depth):
        self.min_user_speed = min_speed
        self.max_user_speed = max_speed
        self.min_handy_depth = min_depth
        self.max_handy_depth = max_depth

    def _send_command(self, path, body=None):
        if not self.handy_key:
            return
        headers = {"Content-Type": "application/json", "X-Connection-Key": self.handy_key}
        try:
            requests.put(f"{self.base_url}{path}", headers=headers, json=body or {}, timeout=10)
        except requests.exceptions.RequestException as e:
            print(f"[HANDY ERROR] Problem: {e}", file=sys.stderr)

    def _safe_percent(self, p):
        try:
            p = float(p)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(100.0, p))

    def move(self, speed, depth, stroke_range):
        """
        A simpler move function that expects complete instructions from the AI.
        It scales the provided values to the user's calibrated limits.
        """
        if not self.handy_key:
            return

        # A speed of 0 is a special command to stop all movement.
        if speed is not None and speed == 0:
            self._send_command("hamp/stop")
            self.last_stroke_speed = 0
            self.last_relative_speed = 0
            return

        # Handle cases where the AI might still send null values
        if speed is None or depth is None or stroke_range is None:
            print("⚠️ Incomplete move received from AI, ignoring.")
            return

        self._send_command("mode", {"mode": 0})
        self._send_command("hamp/start")

        # Set slide range based on depth and stroke_range
        relative_pos_pct = self._safe_percent(depth)
        absolute_center_pct = self.min_handy_depth + (self.max_handy_depth - self.min_handy_depth) * (relative_pos_pct / 100.0)
        calibrated_range_width = self.max_handy_depth - self.min_handy_depth
        
        relative_range_pct = self._safe_percent(stroke_range)
        span_abs = (calibrated_range_width * (relative_range_pct / 100.0)) / 2.0
        
        min_zone_abs = absolute_center_pct - span_abs
        max_zone_abs = absolute_center_pct + span_abs
        
        clamped_min_zone = max(self.min_handy_depth, min_zone_abs)
        clamped_max_zone = min(self.max_handy_depth, max_zone_abs)
        
        slide_min = round(100 - clamped_max_zone)
        slide_max = round(100 - clamped_min_zone)

        if slide_min >= slide_max:
            slide_max = slide_min + 2
        
        slide_max = min(100, slide_max)
        slide_min = max(0, slide_min)

        self._send_command("slide", {"min": slide_min, "max": slide_max})
        
        # Calculate and set the final velocity
        relative_speed_pct = self._safe_percent(speed)
        speed_range_width = self.max_user_speed - self.min_user_speed
        final_physical_speed = self.min_user_speed + (speed_range_width * (relative_speed_pct / 100.0))
        final_physical_speed = int(round(final_physical_speed))
        
        self._send_command("hamp/velocity", {"velocity": final_physical_speed})

        # Update state variables for the next command
        self.last_stroke_speed = final_physical_speed
        self.last_relative_speed = relative_speed_pct
        self.last_depth_pos = int(round(relative_pos_pct))

    def stop(self):
        """Stops all movement."""
        self.move(speed=0, depth=None, stroke_range=None)

    def nudge(self, direction, min_depth_pct, max_depth_pct, current_pos_mm):
        JOG_STEP_MM = 2.0
        JOG_VELOCITY_MM_PER_SEC = 20.0
        min_mm = self.FULL_TRAVEL_MM * float(min_depth_pct) / 100.0
        max_mm = self.FULL_TRAVEL_MM * float(max_depth_pct) / 100.0
        
        target_mm = current_pos_mm
        if direction == 'up':
            target_mm = min(current_pos_mm + JOG_STEP_MM, max_mm)
        elif direction == 'down':
            target_mm = max(current_pos_mm - JOG_STEP_MM, min_mm)
        
        self._send_command(
            "hdsp/xava",
            {"position": target_mm, "velocity": JOG_VELOCITY_MM_PER_SEC, "stopOnTarget": True},
        )
        return target_mm

    def get_position_mm(self):
        if not self.handy_key:
            return None
        headers = {"X-Connection-Key": self.handy_key}
        try:
            resp = requests.get(f"{self.base_url}slide/position/absolute", headers=headers, timeout=10)
            data = resp.json()
            return float(data.get("position", 0))
        except requests.exceptions.RequestException as e:
            print(f"[HANDY ERROR] Problem reading position: {e}", file=sys.stderr)
            return None

    def mm_to_percent(self, val):
        return int(round((float(val) / self.FULL_TRAVEL_MM) * 100))