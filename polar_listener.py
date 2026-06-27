"""
polar_listener.py

Connect to a Polar H10 chest strap via BLE and stream heart rate +
RR intervals to a JSONL file with sub-second UTC timestamps.

Each line in the output file is one JSON record:
  {
    "timestamp_utc": "2026-05-11T10:23:45.123456+00:00",
    "type": "hr_measurement",
    "heart_rate_bpm": 72,
    "rr_intervals_ms": [834, 845, 821],
    "energy_expended_kj": null,
    "sensor_contact": "ok"
  }

The "rr_intervals_ms" field is the analytical workhorse — each value is
the time between consecutive heartbeats in milliseconds, sampled at the
strap's full resolution.

Usage:
    python polar_listener.py
    python polar_listener.py --duration 1800       # run for 30 minutes
    python polar_listener.py --output session.jsonl
"""

import argparse
import asyncio
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner


# Standard Bluetooth Heart Rate Service
HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_CHAR_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# H10 advertises its name with this prefix
POLAR_NAME_PREFIX = "Polar H10"


def parse_hr_measurement(data: bytearray) -> dict:
    """
    Decode the Heart Rate Measurement notification per Bluetooth SIG spec.
    
    Byte 0 is a flags byte:
      bit 0: HR value format (0 = uint8, 1 = uint16)
      bits 1-2: Sensor contact status
      bit 3: Energy Expended present
      bit 4: RR intervals present
    """
    flags = data[0]
    hr_format_uint16 = bool(flags & 0x01)
    sensor_contact_supported = bool(flags & 0x04)
    sensor_contact_detected = bool(flags & 0x02)
    energy_expended_present = bool(flags & 0x08)
    rr_intervals_present = bool(flags & 0x10)

    offset = 1

    # Heart rate value
    if hr_format_uint16:
        heart_rate = int.from_bytes(data[offset:offset + 2], "little")
        offset += 2
    else:
        heart_rate = data[offset]
        offset += 1

    # Sensor contact status (only meaningful if supported)
    if not sensor_contact_supported:
        sensor_contact = "unsupported"
    elif sensor_contact_detected:
        sensor_contact = "ok"
    else:
        sensor_contact = "poor"

    # Energy expended (optional)
    energy_expended_kj = None
    if energy_expended_present:
        energy_expended_kj = int.from_bytes(data[offset:offset + 2], "little")
        offset += 2

    # RR intervals (optional, multiple values possible)
    # Each is uint16 in units of 1/1024 second — convert to milliseconds
    rr_intervals_ms = []
    if rr_intervals_present:
        while offset + 1 < len(data):
            rr_1024 = int.from_bytes(data[offset:offset + 2], "little")
            rr_ms = round((rr_1024 / 1024.0) * 1000.0, 2)
            rr_intervals_ms.append(rr_ms)
            offset += 2

    return {
        "heart_rate_bpm": heart_rate,
        "rr_intervals_ms": rr_intervals_ms,
        "energy_expended_kj": energy_expended_kj,
        "sensor_contact": sensor_contact,
    }


async def find_polar_h10():
    """Scan for the Polar H10 and return its address. None if not found."""
    print("Scanning for Polar H10...")
    devices = await BleakScanner.discover(timeout=10.0)
    for device in devices:
        name = device.name or ""
        if name.startswith(POLAR_NAME_PREFIX):
            print(f"Found {name} at {device.address}")
            return device.address
    return None


async def listen_to_polar(address: str, output_path: Path, duration_sec: int):
    """Connect to the H10 and stream HR measurements until duration elapses."""
    print(f"Connecting to {address}...")

    record_count = 0
    rr_count = 0
    start_time = datetime.now(timezone.utc)

    output_file = output_path.open("a")

    def handle_hr_notification(_characteristic, data: bytearray):
        nonlocal record_count, rr_count
        try:
            parsed = parse_hr_measurement(data)
            record = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "type": "hr_measurement",
                **parsed,
            }
            output_file.write(json.dumps(record) + "\n")
            output_file.flush()

            record_count += 1
            rr_count += len(parsed["rr_intervals_ms"])

            # Print a one-line status every notification
            rr_preview = ", ".join(str(int(rr)) for rr in parsed["rr_intervals_ms"])
            print(f"  HR {parsed['heart_rate_bpm']:>3} bpm  "
                  f"RR [{rr_preview}] ms  "
                  f"contact: {parsed['sensor_contact']}")
        except Exception as e:
            print(f"  [parse error] {e}", file=sys.stderr)

    async with BleakClient(address) as client:
        print(f"Connected. Subscribing to heart rate notifications...\n")
        await client.start_notify(HEART_RATE_MEASUREMENT_CHAR_UUID,
                                  handle_hr_notification)

        # Wait for either the duration or a Ctrl-C
        try:
            await asyncio.sleep(duration_sec)
        except asyncio.CancelledError:
            pass

        await client.stop_notify(HEART_RATE_MEASUREMENT_CHAR_UUID)

    output_file.close()

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    print(f"\nSession complete.")
    print(f"  Duration: {elapsed:.0f} seconds")
    print(f"  HR records written: {record_count}")
    print(f"  Total RR intervals captured: {rr_count}")
    print(f"  Output: {output_path}")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=120,
                        help="Capture duration in seconds (default 120)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSONL path (default: timestamped file)")
    parser.add_argument("--address", type=str, default=None,
                        help="Polar H10 BLE address (skip scan if provided)")
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path(f"polar_{stamp}.jsonl")

    address = args.address
    if not address:
        address = await find_polar_h10()
        if not address:
            print("ERROR: No Polar H10 found. Is the strap on with wet electrodes?")
            sys.exit(1)

    # Graceful shutdown on Ctrl-C
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def request_stop():
        print("\n[Ctrl-C received, stopping...]")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_stop)

    listen_task = asyncio.create_task(
        listen_to_polar(address, output_path, args.duration)
    )

    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait(
        {listen_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
