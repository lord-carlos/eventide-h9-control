# USB Audio Diagnostics

This directory contains diagnostic scripts to investigate and potentially fix USB audio buffer overrun issues on Raspberry Pi with the XONE 96 audio interface.

## The Problem

The XONE 96 (and other USB audio devices) can experience **USB buffer overruns** on Raspberry Pi 5:

- Audio stream appears to work but returns **stale data** (identical frames)
- BPM detection reports the same value repeatedly
- Stream doesn't raise errors but is effectively dead
- Logs show: `xhci-hcd: WARN: buffer overrun event for slot X ep Y`
- Requires device unplug/replug or Pi restart to recover

## Diagnostic Scripts

### 1. usb_audio_diagnostics.py

**Purpose**: Detect and diagnose stale audio / USB buffer overruns

**Features**:
- Monitors for consecutive identical audio frames (stale data detection)
- Monitors for consecutive silent frames
- Tracks USB buffer overrun errors in dmesg
- Logs all conditions with timestamps
- Provides summary at end

**Usage**:
```bash
# List available devices
uv run python scripts/usb_audio_diagnostics.py --list-devices

# Run diagnostics for 10 minutes (600 seconds)
uv run python scripts/usb_audio_diagnostics.py --duration 600

# Use specific device (check device index with --list-devices)
uv run python scripts/usb_audio_diagnostics.py --device 0 --duration 600

# With verbose logging
cd scripts && python usb_audio_diagnostics.py --device 0 --duration 600 2>&1 | tee diagnostic.log
```

**Expected Output**:
```
19:29:24 DEBUG root: BPM detected: 121.0 (raw: 120.96)
19:29:24 WARNING root: STALE AUDIO DETECTED: 10 consecutive identical frames!
19:29:24 ERROR root: USB buffer overruns detected: 5
...
DIAGNOSTIC SUMMARY
Duration: 300.5 seconds
USB buffer overruns: 12
STALE AUDIO DETECTED - Stream was returning identical frames
```

### 2. auto_recovery_audio.py

**Purpose**: Test auto-recovery from USB buffer overruns

**Features**:
- Same monitoring as diagnostics script
- **Automatically attempts stream restart** when stale data detected
- Exponential backoff between recovery attempts (1s, 2s, 4s)
- Max 3 recovery attempts with 30s cooldown
- Logs all recovery attempts and outcomes

**Usage**:
```bash
# Run with auto-recovery enabled (default)
uv run python scripts/auto_recovery_audio.py --duration 600

# Test without recovery (to see the failure)
uv run python scripts/auto_recovery_audio.py --no-recovery --duration 600

# With specific device
uv run python scripts/auto_recovery_audio.py --device 0 --duration 900
```

**Expected Behavior**:
- When stale data detected, script attempts to close and reopen audio stream
- If successful, stream continues with fresh audio
- If failed after 3 attempts, script stops with error
- Check if this resolves the 5-minute crash issue

### 3. test_beat_detector.py

**Purpose**: Standalone beat detector (existing script, unchanged)

This is the original standalone test for beat detection without UI.

## Root Cause Analysis

From your dmesg output:
```
xhci-hcd xhci-hcd.0: WARN: buffer overrun event for slot 2 ep 4 on endpoint
retire_capture_urb: 86 callbacks suppressed
```

This shows:
1. **USB buffer overruns** on the XHCI (USB 3.0) controller
2. **URB (USB Request Block) completion issues** - USB audio uses isochronous transfers
3. The device stops delivering new audio data but remains "connected"
4. PortAudio/PyAudio don't detect this as an error (stream still "open")

## Testing Procedure

### Phase 1: Confirm the Issue (15 minutes)

```bash
# 1. Run diagnostics to confirm stale data
uv run python scripts/usb_audio_diagnostics.py --duration 300

# Check if you see:
# - "STALE AUDIO DETECTED" messages
# - "USB buffer overruns detected" in summary
```

### Phase 2: Test Auto-Recovery (15 minutes)

```bash
# 2. Test if stream restart fixes it
uv run python scripts/auto_recovery_audio.py --duration 300

# Check if:
# - Recovery attempts are triggered
# - Recovery is successful
# - Stream continues after recovery
```

### Phase 3: Test in Your App (if Phase 2 works)

If auto-recovery works in the test scripts, we need to add the same logic to `beat_detector.py`.

## Next Steps Based on Results

### If diagnostics confirm stale data:
- USB buffer overruns are the root cause
- We need to implement detection + recovery in beat_detector.py

### If auto-recovery works:
- Stream restart logic can recover from USB overruns
- We'll add the same logic to beat_detector.py
- This should fix the 5-minute crash

### If auto-recovery fails:
- USB device requires physical reset (unplug/replug)
- May need:
  - Different USB port (USB 2.0 vs 3.0)
  - Powered USB hub
  - USB autosuspend disabled
  - Kernel parameter tuning

### If no stale data detected:
- Issue might be librosa processing or CPU load
- Check CPU usage during test
- Try reducing librosa hop_length or disabling some features

## System Configuration Recommendations

If USB overruns persist, try these system-level fixes:

### 1. Disable USB Autosuspend
```bash
# Create udev rule
echo 'ACTION=="add", SUBSYSTEM=="usb", ATTR{power/autosuspend}="-1"' | sudo tee /etc/udev/rules.d/50-usb-power-save.rules

# Or disable globally
sudo sh -c 'echo -1 > /sys/bus/usb/devices/usb1/power/autosuspend_delay_ms'
```

### 2. Check USB Port
- Try different USB ports on Pi 5
- USB 2.0 ports (black) might be more stable than USB 3.0 (blue)
- Avoid USB hubs if possible, or use powered hub

### 3. Kernel Parameters
Add to `/boot/cmdline.txt`:
```
usbcore.autosuspend=-1 xhci_hcd.quirks=0x4
```

### 4. ALSA Configuration
Create `/etc/asound.conf`:
```
# Larger buffers for USB audio
pcm.usb_audio {
    type hw
    card 0
    device 0
}

pcm.!default {
    type plug
    slave {
        pcm "usb_audio"
        period_size 4096
        buffer_size 16384
    }
}
```

### 5. CPU Governor
```bash
# Set CPU to performance mode
sudo cpufreq-set -g performance
# Or for permanent change:
echo 'GOVERNOR="performance"' | sudo tee /etc/default/cpufrequtils
```

## Alternative: sounddevice Library

If PyAudio issues persist, we have also added the `sounddevice` library as a dependency. It provides:
- Better XRUN detection via `CallbackFlags`
- More explicit error handling
- Alternative to PyAudio

A sounddevice-based implementation can be created if needed.

## Monitoring During Tests

Run these in separate terminals while testing:

```bash
# Monitor USB errors
watch -n 5 'dmesg | tail -20 | grep -E "overrun|usb|alsa"'

# Monitor CPU usage
top -p $(pgrep -f "python.*usb_audio_diagnostics")

# Monitor system load
watch -n 5 'cat /proc/loadavg'

# Check USB device state
lsusb -t
```

## Reporting Results

After running diagnostics, please report:

1. **Did diagnostics detect stale data?** (Yes/No)
2. **Did auto-recovery work?** (Yes/No - how many successful recoveries?)
3. **USB overruns count** from diagnostic summary
4. **Duration before first stale data** detected
5. **Any other patterns observed**

This will help determine the best fix strategy.
