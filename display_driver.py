# display_driver.py
# Waveshare ESP32-S3-Touch-LCD-4.3 RGB display init.
#
# DRIFT FIX: The horizontal drift over time is caused by PSRAM bandwidth
# starvation — the RGB DMA streams pixels from PSRAM continuously, and when
# WiFi/socket traffic stalls those reads the panel scan position slips.
# Espressif's fix is "bounce buffers": small internal-SRAM staging buffers
# the DMA reads from instead of PSRAM directly. Newer lvgl_micropython
# builds expose this on RGBBus. We try it and fall back gracefully —
# main.py checks BOUNCE_OK to decide whether the periodic reboot fallback
# is still needed.
from micropython import const
import gc
import lvgl as lv
import lcd_bus
import rgb_display_framework as rgb_display
import task_handler

gc.collect()

_WIDTH, _HEIGHT = 800, 480

_BUS_KWARGS = dict(
    hsync=46,
    vsync=3,
    de=5,
    pclk=7,
    data0=14, data1=38, data2=18, data3=17, data4=10,
    data5=39, data6=0, data7=45, data8=48, data9=47, data10=21,
    data11=1, data12=2, data13=42, data14=41, data15=40,
    freq=13000000,
    hsync_front_porch=8,
    hsync_pulse_width=4,
    hsync_back_porch=8,
    vsync_front_porch=8,
    vsync_pulse_width=4,
    vsync_back_porch=8,
    vsync_idle_low=True,
    de_idle_high=False,
    pclk_idle_high=False,
    pclk_active_low=1,
)

# Try bounce buffer first (10 scanlines of internal SRAM staging).
# Different lvgl_micropython versions name this differently, so try both.
BOUNCE_OK = False
rgb_bus = None
for kwarg_name in ("bounce_buffer_size_px", "bounce_buffer_lines"):
    try:
        kwargs = dict(_BUS_KWARGS)
        kwargs[kwarg_name] = _WIDTH * 10 if kwarg_name.endswith("px") else 10
        rgb_bus = lcd_bus.RGBBus(**kwargs)
        BOUNCE_OK = True
        print("RGBBus OK with bounce buffer ({}):".format(kwarg_name), rgb_bus)
        break
    except TypeError:
        continue

if rgb_bus is None:
    rgb_bus = lcd_bus.RGBBus(**_BUS_KWARGS)
    print("RGBBus OK (no bounce buffer support in this build):", rgb_bus)

_FB_BYTES = _WIDTH * _HEIGHT * 2

print("Alloc framebuffer 1:", _FB_BYTES, "bytes")
gc.collect()
_BUF1 = rgb_bus.allocate_framebuffer(_FB_BYTES, lcd_bus.MEMORY_SPIRAM)
gc.collect()

_BUF2 = None
try:
    print("Alloc framebuffer 2:", _FB_BYTES, "bytes")
    _BUF2 = rgb_bus.allocate_framebuffer(_FB_BYTES, lcd_bus.MEMORY_SPIRAM)
    gc.collect()
    print("Dual framebuffer OK")
except Exception as e:
    print("Single framebuffer:", repr(e))

print("Creating display...")
display = rgb_display.RGBDisplayDriver(
    data_bus=rgb_bus,
    display_width=_WIDTH,
    display_height=_HEIGHT,
    frame_buffer1=_BUF1,
    frame_buffer2=_BUF2,
    backlight_pin=2,
    color_space=lv.COLOR_FORMAT.RGB565,
    rgb565_byte_swap=False,
)
print("Display OK:", display)
display.set_power(True)
display.init()

# TaskHandler drives LVGL's timer in background
task_handler.TaskHandler()
