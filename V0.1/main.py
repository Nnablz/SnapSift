import io
import os
import shutil
import rawpy
from tkinter import filedialog
import customtkinter as ctk
from PIL import Image, ImageTk, ImageOps

# Constants
SUPPORTED_FORMATS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.arw')

class SnapSiftApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("SnapSift")
        self.geometry("1000x700")
        
        # State variables
        self.source_dir = ""
        self.target_dirs = [] # list of dicts: {'path': '...', 'key': '1'}
        self.images = []
        self.current_index = 0
        self.show_grid = False
        
        self.setup_ui()
        self.bind_keys()
        
    def setup_ui(self):
        # Configure grid
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # --- Top Frame (Controls) ---
        self.top_frame = ctk.CTkFrame(self, height=60, corner_radius=0)
        self.top_frame.grid(row=0, column=0, sticky="ew")
        
        self.btn_src = ctk.CTkButton(self.top_frame, text="Select Source Folder", command=self.select_source)
        self.btn_src.pack(side="left", padx=10, pady=10)
        
        self.btn_add_target = ctk.CTkButton(self.top_frame, text="+ Add Target Folder", command=self.add_target)
        self.btn_add_target.pack(side="left", padx=10, pady=10)

        self.btn_grid = ctk.CTkButton(self.top_frame, text="Toggle Grid", command=self.toggle_grid)
        self.btn_grid.pack(side="right", padx=10, pady=10)
        
        self.targets_frame = ctk.CTkScrollableFrame(self.top_frame, orientation="horizontal", height=40)
        self.targets_frame.pack(side="left", fill="x", expand=True, padx=10, pady=5)
        
        # --- Main Frame (Image) ---
        self.main_frame = ctk.CTkFrame(self, corner_radius=0)
        self.main_frame.grid(row=1, column=0, sticky="nsew")
        
        self.image_label = ctk.CTkLabel(self.main_frame, text="Select a source folder to begin")
        self.image_label.pack(expand=True, fill="both")
        
        # Grid overlay canvas (transparent if possible, or drawn on image directly)
        # CustomTkinter labels don't support drawing directly like Canvas, 
        # so we will use a Canvas for the image display to support drawing the grid.
        self.image_label.pack_forget()
        
        self.canvas = ctk.CTkCanvas(self.main_frame, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(expand=True, fill="both")
        self.canvas.bind("<Configure>", self.on_resize)
        
        self.canvas_text = self.canvas.create_text(500, 350, text="Select a source folder to begin", fill="white", font=("Arial", 16))
        self.photo_image = None # Keep reference
        self.image_item = None
        self.grid_items = []
        
        # --- Bottom Frame (Status) ---
        self.bottom_frame = ctk.CTkFrame(self, height=30, corner_radius=0)
        self.bottom_frame.grid(row=2, column=0, sticky="ew")
        
        self.lbl_status = ctk.CTkLabel(self.bottom_frame, text="Ready")
        self.lbl_status.pack(side="left", padx=10)
        
        self.lbl_progress = ctk.CTkLabel(self.bottom_frame, text="0 / 0")
        self.lbl_progress.pack(side="right", padx=10)
        
    def bind_keys(self):
        self.bind("<Left>", lambda e: self.handle_sort_key("Bad"))
        self.bind("<Up>", lambda e: self.handle_sort_key("Maybe"))
        self.bind("<Right>", lambda e: self.handle_sort_key("Good"))
        self.bind("<Down>", lambda e: self.next_image())
        self.bind("<BackSpace>", lambda e: self.prev_image())
        for i in range(1, 10):
            self.bind(str(i), self.handle_number_key)

    def handle_sort_key(self, name):
        if not self.images or self.current_index >= len(self.images):
            return
        target = next((t for t in self.target_dirs if t.get('name') == name), None)
        if target:
            self.move_current_image(target['path'])
            
    def select_source(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.source_dir = folder
            self.setup_auto_targets()
            self.load_images()
            
    def setup_auto_targets(self):
        self.target_dirs = []
        for name, key_symbol in [("Bad", "←"), ("Maybe", "↑"), ("Good", "→")]:
            path = os.path.join(self.source_dir, name)
            os.makedirs(path, exist_ok=True)
            self.target_dirs.append({'path': path, 'key': key_symbol, 'name': name})
        self.update_targets_ui()
            
    def add_target(self):
        folder = filedialog.askdirectory(title="Select Target Folder")
        if folder:
            key_num = len(self.target_dirs) + 1
            if key_num > 9:
                return # Only support 1-9
            
            self.target_dirs.append({
                'path': folder,
                'key': str(key_num)
            })
            self.update_targets_ui()
            
    def update_targets_ui(self):
        for widget in self.targets_frame.winfo_children():
            widget.destroy()
            
        for t in self.target_dirs:
            name = t.get('name') or os.path.basename(t['path']) or t['path']
            lbl = ctk.CTkLabel(self.targets_frame, text=f"[{t['key']}] {name}", fg_color="gray30", corner_radius=5, padx=5)
            lbl.pack(side="left", padx=5)

    def load_images(self):
        self.images = []
        for file in os.listdir(self.source_dir):
            if file.lower().endswith(SUPPORTED_FORMATS):
                self.images.append(os.path.join(self.source_dir, file))
        
        self.images.sort()
        self.current_index = 0
        self.update_display()
        
    def update_display(self):
        if not self.images:
            self.canvas.itemconfig(self.canvas_text, text="No images found in source directory.")
            if self.image_item:
                self.canvas.delete(self.image_item)
                self.image_item = None
            self.photo_image = None
            self.draw_grid()
            self.lbl_progress.configure(text="0 / 0")
            return
            
        if self.current_index >= len(self.images):
            self.current_index = len(self.images) - 1
        if self.current_index < 0:
            self.current_index = 0
            
        img_path = self.images[self.current_index]
        self.lbl_progress.configure(text=f"{self.current_index + 1} / {len(self.images)}")
        self.lbl_status.configure(text=os.path.basename(img_path))
        
        self.draw_image()
        
    def draw_image(self, event=None):
        if not self.images or self.current_index >= len(self.images):
            return
            
        img_path = self.images[self.current_index]
        
        try:
            if img_path.lower().endswith('.arw'):
                with rawpy.imread(img_path) as raw:
                    try:
                        thumb = raw.extract_thumb()
                        if thumb.format == rawpy.ThumbFormat.JPEG:
                            img = Image.open(io.BytesIO(thumb.data))
                            img = ImageOps.exif_transpose(img)
                        elif thumb.format == rawpy.ThumbFormat.BITMAP:
                            img = Image.fromarray(thumb.data)
                        else:
                            rgb = raw.postprocess(use_camera_wb=True, half_size=True)
                            img = Image.fromarray(rgb)
                    except rawpy.LibRawNoThumbnailError:
                        rgb = raw.postprocess(use_camera_wb=True, half_size=True)
                        img = Image.fromarray(rgb)
            else:
                img = Image.open(img_path)
                img = ImageOps.exif_transpose(img)
            
            # Resize logic
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            
            if canvas_width > 1 and canvas_height > 1:
                img.thumbnail((canvas_width, canvas_height), Image.Resampling.LANCZOS)
                
            self.photo_image = ImageTk.PhotoImage(img)
            
            if self.image_item:
                self.canvas.delete(self.image_item)
                
            x = canvas_width // 2
            y = canvas_height // 2
            self.image_item = self.canvas.create_image(x, y, image=self.photo_image, anchor="center")
            self.canvas.itemconfig(self.canvas_text, text="")
            
            self.draw_grid()
            
        except Exception as e:
            self.canvas.itemconfig(self.canvas_text, text=f"Error loading image: {e}")
            if self.image_item:
                self.canvas.delete(self.image_item)

    def on_resize(self, event):
        # Debounce or just raw call
        self.draw_image()
        
    def toggle_grid(self):
        self.show_grid = not self.show_grid
        self.draw_grid()
        
    def draw_grid(self):
        for item in self.grid_items:
            self.canvas.delete(item)
        self.grid_items.clear()
        
        if not self.show_grid or not self.photo_image:
            return
            
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()
        
        img_w = self.photo_image.width()
        img_h = self.photo_image.height()
        
        # Calculate image top-left
        start_x = (canvas_w - img_w) / 2
        start_y = (canvas_h - img_h) / 2
        
        # Draw rule of thirds
        third_w = img_w / 3
        third_h = img_h / 3
        
        # Vertical lines
        l1 = self.canvas.create_line(start_x + third_w, start_y, start_x + third_w, start_y + img_h, fill="red", dash=(4, 4))
        l2 = self.canvas.create_line(start_x + 2*third_w, start_y, start_x + 2*third_w, start_y + img_h, fill="red", dash=(4, 4))
        
        # Horizontal lines
        l3 = self.canvas.create_line(start_x, start_y + third_h, start_x + img_w, start_y + third_h, fill="red", dash=(4, 4))
        l4 = self.canvas.create_line(start_x, start_y + 2*third_h, start_x + img_w, start_y + 2*third_h, fill="red", dash=(4, 4))
        
        self.grid_items.extend([l1, l2, l3, l4])

    def prev_image(self):
        if self.images and self.current_index > 0:
            self.current_index -= 1
            self.update_display()

    def next_image(self):
        if self.images and self.current_index < len(self.images) - 1:
            self.current_index += 1
            self.update_display()
            
    def handle_number_key(self, event):
        if not self.images or self.current_index >= len(self.images):
            return
            
        key = event.keysym
        target = next((t for t in self.target_dirs if t['key'] == key), None)
        
        if target:
            self.move_current_image(target['path'])
            
    def move_current_image(self, target_dir):
        img_path = self.images[self.current_index]
        filename = os.path.basename(img_path)
        dest_path = os.path.join(target_dir, filename)
        
        try:
            shutil.move(img_path, dest_path)
            self.images.pop(self.current_index)
            # update index if we were at the end
            if self.current_index >= len(self.images) and len(self.images) > 0:
                self.current_index -= 1
                
            self.update_display()
        except Exception as e:
            self.lbl_status.configure(text=f"Error moving file: {e}")

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = SnapSiftApp()
    app.mainloop()
