"""
ble_scanner.py

Continuous BLE scanner. Scans for 30 seconds and prints every BLE device
it sees, with name, MAC address, and signal strength (RSSI).

Purpose: definitive test of whether the Mac can detect the Polar H10
at the BLE radio level, independent of macOS's pairing UI.

Usage:
    python ble_scanner.py
"""

import asyncio
from bleak import BleakScanner


SCAN_DURATION_SECONDS = 30


async def main():
    print(f"Scanning for BLE devices for {SCAN_DURATION_SECONDS} seconds...")
    print("Make sure the Polar H10 is on with damp electrodes.\n")
    print(f"{'Name':<40} {'Address':<20} {'RSSI':>6}")
    print("-" * 70)

    seen = {}

    def detection_callback(device, advertisement_data):
        # Track each device by address; update with latest RSSI
        name = device.name or advertisement_data.local_name or "(unnamed)"
        rssi = advertisement_data.rssi
        if device.address not in seen:
            print(f"{name:<40} {device.address:<20} {rssi:>6}")
        seen[device.address] = (name, rssi)

    async with BleakScanner(detection_callback=detection_callback):
        await asyncio.sleep(SCAN_DURATION_SECONDS)

    print("\n" + "=" * 70)
    print(f"Scan complete. {len(seen)} unique device(s) detected.\n")

    # Highlight any Polar device specifically
    polar_devices = {addr: info for addr, info in seen.items()
                     if "polar" in info[0].lower() or "h10" in info[0].lower()}

    if polar_devices:
        print("POLAR DEVICE(S) FOUND:")
        for addr, (name, rssi) in polar_devices.items():
            print(f"  {name}  ({addr})  signal: {rssi} dBm")
        print("\nThe H10 is detectable. We can proceed to write the listener.")
    else:
        print("No Polar device detected in this scan.")
        print("\nIf you are wearing the strap with damp electrodes:")
        print("  - The strap may be sleeping. Re-wet electrodes and try again.")
        print("  - Another device may be connected to it.")
        print("  - The strap may be defective.")


if __name__ == "__main__":
    asyncio.run(main())
