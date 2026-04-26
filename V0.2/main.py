import io
import os
import json
import shutil
import threading
import rawpy
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image, ImageTk, ImageOps

# ─── Constants ────────────────────────────────────────────────────────────────
VERSION = "0.2.0"

STANDARD_FORMATS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff')
RAW_FORMATS = (
    '.arw', '.srf', '.sr2',  # Sony
    '.cr2', '.cr3',           # Canon
    '.nef', '.nrw',           # Nikon
    '.raf',                   # Fuji
    '.orf',                   # Olympus
    '.rw2',                   # Panasonic
    '.pef', '.dng',           # Pentax / Adobe DNG
    '.rwl',                   # Leica
    '.3fr',                   # Hasselblad
    '.iiq',                   # Phase One
    '.x3f',                   # Sigma
    '.mrw',                   # Minolta
    '.erf',                   # Epson
)
SUPPORTED_FORMATS = STANDARD_FORMATS + RAW_FORMATS

GRID_MODES = ["Off", "Rule of Thirds", "Golden Ratio", "Center Cross", "Diagonal", "Square"]

DEFAULT_BINDINGS = {
    "sort_bad":    "Left",
    "sort_maybe":  "Up",
    "sort_good":   "Right",
    "next":        "Down",
    "prev":        "BackSpace",
    "toggle_grid": "g",
}

# rawpy flip value → PIL rotation angle (CCW degrees)
RAWPY_FLIP_MAP = {3: 180, 5: 90, 6: 270}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keybindings.json")

ACTION_LABELS = {
    "sort_bad":    "Sort → Bad",
    "sort_maybe":  "Sort → Maybe",
    "sort_good":   "Sort → Good",
    "next":        "Next Image",
    "prev":        "Previous Image",
    "toggle_grid": "Toggle Grid",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_raw(path: str) -> bool:
    return path.lower().endswith(RAW_FORMATS)


def load_image(path: str) -> Image.Image:
    """Load any supported format and return an auto-rotated PIL Image."""
    if is_raw(path):
        with rawpy.imread(path) as raw:
            # Try fast thumbnail path first
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = Image.open(io.BytesIO(thumb.data))
                    img = ImageOps.exif_transpose(img)
                    return img
                elif thumb.format == rawpy.ThumbFormat.BITMAP:
                    return Image.fromarray(thumb.data)
            except rawpy.LibRawNoThumbnailError:
                pass
            # Full decode fallback (slower)
            rgb = raw.postprocess(use_camera_wb=True, half_size=True)
            img = Image.fromarray(rgb)
            flip = getattr(raw.sizes, 'flip', 0)
            angle = RAWPY_FLIP_MAP.get(flip, 0)
            if angle:
                img = img.rotate(-angle, expand=True)
            return img
    else:
        img = Image.open(path)
        return ImageOps.exif_transpose(img)


# ─── Look-ahead Image Cache ───────────────────────────────────────────────────

class ImageCache:
    LOOKAHEAD = 2

    def __init__(self, size_fn):
        self._cache: dict = {}
        self._lock = threading.Lock()
        self._size_fn = size_fn  # callable → (w, h)

    def get(self, path: str):
        with self._lock:
            return self._cache.get(path)

    def preload(self, paths: list):
        def _worker():
            for p in paths:
                with self._lock:
                    if p in self._cache:
                        continue
                try:
                    img = load_image(p)
                    w, h = self._size_fn()
                    if w > 1 and h > 1:
                        img.thumbnail((w, h), Image.Resampling.LANCZOS)
                    with self._lock:
                        self._cache[p] = img
                except Exception:
                    pass
        threading.Thread(target=_worker, daemon=True).start()

    def evict(self, keep: list):
        keep_set = set(keep)
        with self._lock:
            for k in list(self._cache):
                if k not in keep_set:
                    del self._cache[k]


# ─── Gamepad Poller ───────────────────────────────────────────────────────────

class GamepadPoller(threading.Thread):
    """Background thread polling pygame joystick events."""

    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self._running = True

    def run(self):
        try:
            import pygame
            pygame.init()
            pygame.joystick.init()
        except Exception:
            return

        joystick = None
        while self._running:
            try:
                import pygame
                count = pygame.joystick.get_count()
                if count > 0 and joystick is None:
                    joystick = pygame.joystick.Joystick(0)
                    joystick.init()
                elif count == 0:
                    joystick = None

                for event in pygame.event.get():
                    if event.type == pygame.JOYHATMOTION:
                        hat = event.value
                        if hat == (-1, 0):   self.callback("sort_bad")
                        elif hat == (1, 0):  self.callback("sort_good")
                        elif hat == (0, 1):  self.callback("sort_maybe")
                        elif hat == (0, -1): self.callback("next")
                    elif event.type == pygame.JOYBUTTONDOWN:
                        # Xbox layout: A=0, B=1, X=2, Y=3, LB=4, RB=5
                        if event.button == 0:   self.callback("sort_good")
                        elif event.button == 1: self.callback("sort_bad")
                        elif event.button == 2: self.callback("sort_maybe")
                        elif event.button == 3: self.callback("toggle_grid")
                        elif event.button == 4: self.callback("prev")
                        elif event.button == 5: self.callback("next")

                pygame.time.wait(16)
            except Exception:
                pass

    def stop(self):
        self._running = False


# ─── Key Binding Dialog ───────────────────────────────────────────────────────

class KeyBindingDialog(ctk.CTkToplevel):
    def __init__(self, parent, bindings: dict, on_save):
        super().__init__(parent)
        self.title("Key Bindings")
        self.geometry("420x380")
        self.resizable(False, False)
        self.grab_set()

        self.bindings = dict(bindings)
        self.on_save = on_save
        self._capturing = None
        self._btns: dict = {}

        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Key Bindings",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(16, 4))
        ctk.CTkLabel(self, text="Click a button then press any key to rebind.",
                     text_color="gray60").pack(pady=(0, 10))

        frame = ctk.CTkFrame(self)
        frame.pack(fill="both", expand=True, padx=16, pady=4)

        for action, label in ACTION_LABELS.items():
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=5)
            ctk.CTkLabel(row, text=label, width=160, anchor="w").pack(side="left")
            btn = ctk.CTkButton(
                row, text=self.bindings.get(action, "?"), width=110,
                command=lambda a=action: self._start_capture(a)
            )
            btn.pack(side="right")
            self._btns[action] = btn

        ctk.CTkButton(self, text="Save & Close", command=self._save,
                      fg_color="#2a7a2a", hover_color="#1f5e1f").pack(pady=14)
        self.bind("<KeyPress>", self._on_key)

    def _start_capture(self, action):
        self._capturing = action
        self._btns[action].configure(text="Press any key…", fg_color="#555555")

    def _on_key(self, event):
        if self._capturing is None:
            return
        key = event.keysym
        self.bindings[self._capturing] = key
        self._btns[self._capturing].configure(text=key, fg_color=("gray70", "gray30"))
        self._capturing = None

    def _save(self):
        self.on_save(self.bindings)
        self.destroy()


# ─── Main Application ─────────────────────────────────────────────────────────

class SnapSiftApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"SnapSift  v{VERSION}")
        self.geometry("1100x750")
        self.minsize(640, 480)

        # App state
        self.source_dir = ""
        self.target_dirs = []
        self.images: list = []
        self.current_index = 0
        self.grid_mode = 0

        # Key bindings
        self.bindings = dict(DEFAULT_BINDINGS)
        self._load_bindings()

        # Infrastructure
        self.cache = ImageCache(self._canvas_size)
        self._resize_job = None
        self.photo_image = None
        self.image_item = None
        self.grid_items: list = []

        self._setup_ui()
        self._bind_keys()
        self._start_gamepad()

    # ── Config ───────────────────────────────────────────────────────────────

    def _load_bindings(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    self.bindings.update(json.load(f))
            except Exception:
                pass

    def _save_bindings(self, new_bindings: dict):
        self.bindings = new_bindings
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.bindings, f, indent=2)
        except Exception:
            pass
        self._bind_keys()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Top bar (minimalist)
        top = ctk.CTkFrame(self, height=48, corner_radius=0, fg_color="#2a2a2a")
        top.grid(row=0, column=0, sticky="ew")
        top.grid_propagate(False)

        # Source folder button – slim
        ctk.CTkButton(top, text="📂 Source",
                      command=self.select_source, width=120,
                      fg_color="#3b3b3b", hover_color="#4c4c4c",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=6, pady=8)

        # Grid toggle – icon only
        self.btn_grid = ctk.CTkButton(top, text="⋯", width=40,
                                      command=self.cycle_grid,
                                      fg_color="#3b3b3b", hover_color="#4c4c4c",
                                      font=ctk.CTkFont(size=16))
        self.btn_grid.pack(side="right", padx=6, pady=8)

        # Settings (bindings) – gear icon
        ctk.CTkButton(top, text="⚙", width=40,
                      command=self.open_bindings,
                      fg_color="#3b3b3b", hover_color="#4c4c4c",
                      font=ctk.CTkFont(size=16)).pack(side="right", padx=2, pady=8)

        # Targets scroll area (keep for custom targets)
        self.targets_frame = ctk.CTkScrollableFrame(top, orientation="horizontal", height=40)
        self.targets_frame.pack(side="left", fill="x", expand=True, padx=8, pady=8)

        # ── Canvas
        main = ctk.CTkFrame(self, corner_radius=0, fg_color="#1a1a1a")
        main.grid(row=1, column=0, sticky="nsew")

        self.canvas = ctk.CTkCanvas(main, bg="#1a1a1a", highlightthickness=0)
        self.canvas.pack(expand=True, fill="both")
        self.canvas.bind("<Configure>", self._on_resize)

        self.canvas_text = self.canvas.create_text(
            500, 350,
            text="📂   Select a source folder to begin",
            fill="#555555", font=("Inter", 14)
        )

        # ── Bottom bar – minimalist
        bot = ctk.CTkFrame(self, height=28, corner_radius=0, fg_color="#2a2a2a")
        bot.grid(row=2, column=0, sticky="ew")
        bot.grid_propagate(False)

        self.lbl_status = ctk.CTkLabel(bot, text="Ready", anchor="w",
                                       font=ctk.CTkFont(size=12))
        self.lbl_status.pack(side="left", padx=12)

        self.lbl_progress = ctk.CTkLabel(bot, text="0 / 0", anchor="e",
                                         font=ctk.CTkFont(size=12))
        self.lbl_progress.pack(side="right", padx=12)

    def _refresh_hint(self):
        # Minimal UI does not show hints; optional overlay can be added later.
        pass

    # ── Keys ─────────────────────────────────────────────────────────────────

    def _bind_keys(self):
        # Clear old bindings (best-effort)
        for action, key in {**DEFAULT_BINDINGS, **self.bindings}.items():
            try:
                self.unbind(f"<{key}>")
            except Exception:
                pass

        mapping = {
            "sort_bad":    lambda e: self._sort("Bad"),
            "sort_maybe":  lambda e: self._sort("Maybe"),
            "sort_good":   lambda e: self._sort("Good"),
            "next":        lambda e: self.next_image(),
            "prev":        lambda e: self.prev_image(),
            "toggle_grid": lambda e: self.cycle_grid(),
        }
        for action, fn in mapping.items():
            key = self.bindings.get(action, DEFAULT_BINDINGS[action])
            try:
                self.bind(f"<{key}>", fn)
            except Exception:
                pass

        for i in range(1, 10):
            self.bind(str(i), self._handle_number_key)

        self._refresh_hint()

    def open_bindings(self):
        KeyBindingDialog(self, self.bindings, self._save_bindings)

    # ── Gamepad ──────────────────────────────────────────────────────────────

    def _start_gamepad(self):
        def _dispatch(action):
            self.after(0, lambda a=action: self._gamepad_action(a))
        self._gamepad = GamepadPoller(_dispatch)
        self._gamepad.start()

    def _gamepad_action(self, action: str):
        dispatch = {
            "sort_bad":    lambda: self._sort("Bad"),
            "sort_maybe":  lambda: self._sort("Maybe"),
            "sort_good":   lambda: self._sort("Good"),
            "next":        self.next_image,
            "prev":        self.prev_image,
            "toggle_grid": self.cycle_grid,
        }
        fn = dispatch.get(action)
        if fn:
            fn()

    # ── Source / Targets ─────────────────────────────────────────────────────

    def select_source(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.source_dir = folder
            self._setup_auto_targets()
            self._load_images()

    def _setup_auto_targets(self):
        self.target_dirs = []
        for name, sym in [("Bad", "←"), ("Maybe", "↑"), ("Good", "→")]:
            path = os.path.join(self.source_dir, name)
            os.makedirs(path, exist_ok=True)
            self.target_dirs.append({'path': path, 'key': sym, 'name': name})
        self._update_targets_ui()

    def add_target(self):
        folder = filedialog.askdirectory(title="Select Target Folder")
        if not folder:
            return
        num = len(self.target_dirs) + 1
        if num > 9:
            return
        self.target_dirs.append({'path': folder, 'key': str(num)})
        self._update_targets_ui()

    def _update_targets_ui(self):
        for w in self.targets_frame.winfo_children():
            w.destroy()
        for t in self.target_dirs:
            name = t.get('name') or os.path.basename(t['path']) or t['path']
            ctk.CTkLabel(
                self.targets_frame, text=f"[{t['key']}] {name}",
                fg_color="gray30", corner_radius=5, padx=6
            ).pack(side="left", padx=4)

    # ── Image Loading ─────────────────────────────────────────────────────────

    def _load_images(self):
        self.images = sorted([
            os.path.join(self.source_dir, f)
            for f in os.listdir(self.source_dir)
            if f.lower().endswith(SUPPORTED_FORMATS)
        ])
        self.current_index = 0
        self.cache.evict([])
        self._update_display()

    def _canvas_size(self):
        return self.canvas.winfo_width(), self.canvas.winfo_height()

    def _update_display(self):
        if not self.images:
            self.canvas.itemconfig(self.canvas_text, text="No images found in source directory.")
            if self.image_item:
                self.canvas.delete(self.image_item)
                self.image_item = None
            self.photo_image = None
            self.draw_grid()
            self.lbl_progress.configure(text="0 / 0")
            return

        self.current_index = max(0, min(self.current_index, len(self.images) - 1))
        img_path = self.images[self.current_index]
        self.lbl_progress.configure(text=f"{self.current_index + 1} / {len(self.images)}")
        self.lbl_status.configure(text=os.path.basename(img_path))
        self._draw_image()

        # Kick off look-ahead preload
        ahead = self.images[self.current_index + 1: self.current_index + 1 + ImageCache.LOOKAHEAD]
        keep = [img_path] + ahead
        self.cache.evict(keep)
        self.cache.preload(ahead)

    def _draw_image(self, event=None):
        if not self.images:
            return
        img_path = self.images[self.current_index]
        cw, ch = self._canvas_size()
        if cw <= 1 or ch <= 1:
            return

        try:
            cached = self.cache.get(img_path)
            if cached:
                img = cached.copy()
            else:
                img = load_image(img_path)

            img.thumbnail((cw, ch), Image.Resampling.LANCZOS)
            self.photo_image = ImageTk.PhotoImage(img)

            if self.image_item:
                self.canvas.delete(self.image_item)

            self.image_item = self.canvas.create_image(
                cw // 2, ch // 2, image=self.photo_image, anchor="center"
            )
            self.canvas.itemconfig(self.canvas_text, text="")
            self.draw_grid()

        except Exception as e:
            self.canvas.itemconfig(self.canvas_text, text=f"Error loading image: {e}")
            if self.image_item:
                self.canvas.delete(self.image_item)

    def _on_resize(self, event):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._draw_image)

    # ── Grid ─────────────────────────────────────────────────────────────────

    def cycle_grid(self):
        self.grid_mode = (self.grid_mode + 1) % len(GRID_MODES)
        self.btn_grid.configure(text=f"Grid: {GRID_MODES[self.grid_mode]}")
        self.draw_grid()

    def draw_grid(self):
        for item in self.grid_items:
            self.canvas.delete(item)
        self.grid_items.clear()

        if self.grid_mode == 0 or not self.photo_image:
            return

        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        iw = self.photo_image.width()
        ih = self.photo_image.height()
        sx = (cw - iw) / 2
        sy = (ch - ih) / 2
        ex = sx + iw
        ey = sy + ih
        c = "#ffffff"
        d = (4, 4)
        add = lambda *args, **kw: self.grid_items.append(self.canvas.create_line(*args, **kw))

        m = self.grid_mode
        if m == 1:  # Rule of Thirds
            for i in (1, 2):
                x, y = sx + iw * i / 3, sy + ih * i / 3
                add(x, sy, x, ey, fill=c, dash=d)
                add(sx, y, ex, y, fill=c, dash=d)

        elif m == 2:  # Golden Ratio (φ)
            phi = 0.6180339887
            for frac in (phi, 1 - phi):
                x, y = sx + iw * frac, sy + ih * frac
                add(x, sy, x, ey, fill="#ffd700", dash=d)
                add(sx, y, ex, y, fill="#ffd700", dash=d)

        elif m == 3:  # Center Cross
            cx, cy = sx + iw / 2, sy + ih / 2
            add(cx, sy, cx, ey, fill=c, dash=d)
            add(sx, cy, ex, cy, fill=c, dash=d)

        elif m == 4:  # Diagonal
            add(sx, sy, ex, ey, fill=c, dash=d)
            add(ex, sy, sx, ey, fill=c, dash=d)

        elif m == 5:  # Uniform Square Grid (3×3)
            for i in (1, 2):
                add(sx + iw * i / 3, sy, sx + iw * i / 3, ey, fill=c, dash=d)
                add(sx, sy + ih * i / 3, ex, sy + ih * i / 3, fill=c, dash=d)

    # ── Navigation ───────────────────────────────────────────────────────────

    def next_image(self):
        if self.images and self.current_index < len(self.images) - 1:
            self.current_index += 1
            self._update_display()

    def prev_image(self):
        if self.images and self.current_index > 0:
            self.current_index -= 1
            self._update_display()

    # ── Sorting ──────────────────────────────────────────────────────────────

    def _sort(self, name: str):
        if not self.images or self.current_index >= len(self.images):
            return
        target = next((t for t in self.target_dirs if t.get('name') == name), None)
        if target:
            self._move_current(target['path'])

    def _handle_number_key(self, event):
        if not self.images or self.current_index >= len(self.images):
            return
        target = next((t for t in self.target_dirs if t['key'] == event.keysym), None)
        if target:
            self._move_current(target['path'])

    def _move_current(self, target_dir: str):
        img_path = self.images[self.current_index]
        dest = os.path.join(target_dir, os.path.basename(img_path))
        try:
            shutil.move(img_path, dest)
            self.images.pop(self.current_index)
            if self.current_index >= len(self.images) and self.images:
                self.current_index -= 1
            self._update_display()
        except Exception as e:
            self.lbl_status.configure(text=f"Move error: {e}")

    # ── Cleanup ──────────────────────────────────────────────────────────────

    def destroy(self):
        if hasattr(self, '_gamepad'):
            self._gamepad.stop()
        super().destroy()


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = SnapSiftApp()
    app.mainloop()
