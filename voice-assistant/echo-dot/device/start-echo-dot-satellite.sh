#!/system/bin/sh
# Magisk service.d launcher for the EchoMuse hardware daemon.
# The Beelink remains the controller; this process only exposes Dot hardware.

export AEC_ENABLED=true
export AGC_ENABLED=false
export MIC_GAIN_DB=24
export STARTUP_VOLUME=65
export BLE_PROXY_ENABLED=false
export OWW_ON_DEVICE=off

log=/data/local/tmp/echomuse.log

echo "$(date) launcher invoked pid=$$ boot_completed=$(getprop sys.boot_completed)" >>"$log"

# Bound persistent debug output across restarts without writing continuously
# to a second location. The live log stays useful over ADB during bring-up.
if [ -f "$log" ] && [ "$(busybox stat -c %s "$log" 2>/dev/null)" -gt 262144 ]; then
    busybox tail -c 131072 "$log" > "${log}.trim"
    mv "${log}.trim" "$log"
fi

# Fire OS can leave sys.boot_completed unset for minutes when its disabled
# Alexa/OOBE packages are not present to finish the stock UI sequence. Magisk
# service.d is already the non-blocking late-start phase, so a bounded hardware
# settle is safer than turning that cosmetic property into a daemon deadlock.
sleep 20

if ps | grep '/data/local/bin/server' | grep -v grep >/dev/null; then
    exit 0
fi

busybox setsid /data/local/bin/server >>"$log" 2>&1 </dev/null &
