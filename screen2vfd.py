import serial
import struct
import time
import threading
import numpy as np
import mss
from PIL import Image, ImageEnhance
import dearpygui.dearpygui as dpg

# Global state
ser = None
stop_event = threading.Event()

def init_vfd(ser_conn):
    # Reset
    ser_conn.write(b'\x1b\x0b')
    time.sleep(0.1)
    # Clear screen
    ser_conn.write(b'\x1b\x0c')
    
    # Get current VFD brightness setting or default to 4
    try:
        vfd_b = dpg.get_value("vfd_brightness")
    except:
        vfd_b = 4
        
    ser_conn.write(b'\x1b\x20' + struct.pack(">B", vfd_b))
    
    # Turn on
    ser_conn.write(b'\x1b\x21\x01')
    time.sleep(0.1)

def send_frame(ser_conn, img):
    # Convert image to numpy array of booleans
    img_array = np.array(img) > 0
    
    # Reshape to group every 8 rows: (4, 8, 160)
    reshaped = img_array.reshape((4, 8, 160))
    
    # The VFD wants MSB at the top.
    weights = np.array([128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8).reshape(8, 1)
    
    # Multiply and sum along the 8-row axis to get bytes
    bytes_array = np.sum(reshaped * weights, axis=1, dtype=np.uint8) # shape (4, 160)
    
    # VFD wants column-major, so transpose to (160, 4)
    vfd_buffer = bytes_array.T
    buffer = vfd_buffer.tobytes()

    # Command: 0x1b 0x2e <x_start:u16> <y_start:u8> <w:u16> <y_end:u8> <data...>
    cmd = b'\x1b\x2e' + struct.pack(">HBHB", 0, 0, 160, 3)
    try:
        ser_conn.write(cmd + buffer)
    except Exception as e:
        print(f"Error sending frame: {e}")

def capture_loop():
    sct = mss.mss()
    while not stop_event.is_set():
        try:
            # Check if DearPyGui is still running and capture is enabled
            if not dpg.is_dearpygui_running():
                break
                
            enabled = dpg.get_value("capture_enabled")
        except:
            enabled = False
            
        global ser
        if enabled and ser is not None and ser.is_open:
            start_time = time.time()
            
            try:
                brightness = dpg.get_value("brightness")
                contrast = dpg.get_value("contrast")
                dither = dpg.get_value("dithering")
                threshold = dpg.get_value("threshold")
                fps = dpg.get_value("fps")
                monitor_str = dpg.get_value("monitor_select")
                scale_mode = dpg.get_value("scale_mode")
            except:
                time.sleep(0.1)
                continue
            
            # Extract monitor index (e.g. "Monitor 1 (...)")
            try:
                monitor_idx = int(monitor_str.split()[1])
            except:
                monitor_idx = 1
                
            # Capture the selected screen using mss for speed
            monitor = sct.monitors[monitor_idx]
            sct_img = sct.grab(monitor)
            screen = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            if scale_mode == "Maintain Aspect Ratio":
                scale = min(160 / screen.width, 32 / screen.height)
                new_w = max(1, int(screen.width * scale))
                new_h = max(1, int(screen.height * scale))
                resized = screen.resize((new_w, new_h), Image.Resampling.BILINEAR)
                
                screen = Image.new("RGB", (160, 32), (0, 0, 0))
                offset_x = (160 - new_w) // 2
                offset_y = (32 - new_h) // 2
                screen.paste(resized, (offset_x, offset_y))
            else:
                # Use BILINEAR instead of LANCZOS for much faster resizing
                screen = screen.resize((160, 32), Image.Resampling.BILINEAR)
            
            if brightness != 1.0:
                screen = ImageEnhance.Brightness(screen).enhance(brightness)
            if contrast != 1.0:
                screen = ImageEnhance.Contrast(screen).enhance(contrast)
                
            if dither:
                screen = screen.convert('1')
            else:
                screen = screen.convert('L')
                screen = screen.point(lambda p: 255 if p > threshold else 0, mode='1')
                
            send_frame(ser, screen)
            
            elapsed = time.time() - start_time
            sleep_time = (1.0 / fps) - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
        else:
            time.sleep(0.1)

def connect_callback():
    global ser
    port = dpg.get_value("com_port")
    try:
        if ser is not None and ser.is_open:
            ser.close()
        ser = serial.Serial(port, 115200, rtscts=True)
        init_vfd(ser)
        dpg.set_value("status_text", f"Connected to {port}")
        dpg.hide_item("btn_connect")
        dpg.show_item("btn_disconnect")
    except Exception as e:
        dpg.set_value("status_text", f"Error: {e}")

def disconnect_callback():
    global ser
    if ser is not None and ser.is_open:
        try:
            ser.write(b'\x1b\x0c') # Clear screen
            ser.close()
        except:
            pass
        ser = None
    dpg.set_value("status_text", "Disconnected")
    dpg.hide_item("btn_disconnect")
    dpg.show_item("btn_connect")
    dpg.set_value("capture_enabled", False)

def vfd_brightness_callback(sender, app_data):
    global ser
    if ser is not None and ser.is_open:
        try:
            # Set brightness (0-4)
            ser.write(b'\x1b\x20' + struct.pack(">B", app_data))
        except:
            pass

def reset_image_settings_callback():
    dpg.set_value("brightness", 1.0)
    dpg.set_value("contrast", 1.0)

def main():
    dpg.create_context()
    
    with mss.mss() as sct:
        monitors = [f"Monitor {i} ({m['width']}x{m['height']})" for i, m in enumerate(sct.monitors[1:], 1)]
    
    with dpg.window(label="VFD Controller", width=420, height=550, no_close=True, no_collapse=True) as main_window:
        dpg.add_text("Disconnected", tag="status_text", color=[255, 255, 0])
        dpg.add_separator()
        
        with dpg.group(horizontal=True):
            dpg.add_input_text(label="COM Port", default_value="COM2", tag="com_port", width=100)
            dpg.add_button(label="Connect", callback=connect_callback, tag="btn_connect")
            dpg.add_button(label="Disconnect", callback=disconnect_callback, tag="btn_disconnect", show=False)
            
        dpg.add_separator()
        dpg.add_checkbox(label="Enable Live Capture", default_value=False, tag="capture_enabled")
        
        dpg.add_separator()
        dpg.add_combo(label="Monitor", items=monitors, default_value=monitors[0] if monitors else "", tag="monitor_select")
        dpg.add_radio_button(label="Scaling Mode", items=["Stretch to Fill", "Maintain Aspect Ratio"], default_value="Stretch to Fill", tag="scale_mode", horizontal=True)
        
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_text("Image Adjustments:")
            dpg.add_button(label="Reset Settings", callback=reset_image_settings_callback)
        dpg.add_slider_float(label="Brightness", default_value=1.0, min_value=0.1, max_value=3.0, tag="brightness")
        dpg.add_slider_float(label="Contrast", default_value=1.0, min_value=0.1, max_value=3.0, tag="contrast")
        
        dpg.add_separator()
        dpg.add_checkbox(label="Use Floyd-Steinberg Dithering", default_value=True, tag="dithering")
        dpg.add_slider_int(label="B&W Threshold (No Dither)", default_value=128, min_value=0, max_value=255, tag="threshold")
        
        dpg.add_separator()
        dpg.add_text("Hardware Settings:")
        dpg.add_slider_int(label="VFD Brightness", default_value=4, min_value=0, max_value=4, tag="vfd_brightness", callback=vfd_brightness_callback)
        dpg.add_slider_float(label="Target Capture FPS", default_value=20.0, min_value=1.0, max_value=30.0, tag="fps")

    dpg.create_viewport(title='screen2vfd', width=450, height=580)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window(main_window, True)
    
    # Start capture thread
    cap_thread = threading.Thread(target=capture_loop, daemon=True)
    cap_thread.start()
    
    # Run main GUI loop
    dpg.start_dearpygui()
    
    # Cleanup on exit
    stop_event.set()
    if ser is not None and ser.is_open:
        try:
            ser.write(b'\x1b\x0c')
            ser.close()
        except:
            pass
            
    dpg.destroy_context()

if __name__ == '__main__':
    main()
