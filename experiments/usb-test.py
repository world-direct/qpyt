import usb
import usb.core
import usb.util

usb.core.logging.basicConfig(level=usb.core.logging.DEBUG)


VENDOR_ID = 0x2c7c
DEVICE_ID = 0x0901

def find_device_handle():
    return usb.core.find(idVendor=VENDOR_ID, idProduct=DEVICE_ID)

dev = find_device_handle()



# based on Wireshare REPL uses the following endpoints:
# 3.7.11 URB_BULK in for data send from device to host
# Device: 7 / Endpoint 11 (0x8b)

# Commands are send on endpoint 3.7.8 URB_BULK out
# Device: 7 / Endpoint 8 (0x08)

if dev is None:
    print("Device not found VENDOR_ID=0x%04x, DEVICE_ID=0x%04x" % (VENDOR_ID, DEVICE_ID))
    exit(1)

# print(dev)

print("\nDiagnostic: Interfaces and endpoints seen by PyUSB:")
for intf in dev.get_active_configuration():
    print(f"Interface {intf.bInterfaceNumber} (endpoints: {[hex(ep.bEndpointAddress) for ep in intf]})")


# Detach kernel drivers from all interfaces if needed

config = dev.get_active_configuration()
detached_interfaces = []
for intf in config:
    iface_num = intf.bInterfaceNumber
    try:
        active = dev.is_kernel_driver_active(iface_num)
        print(f"Interface {iface_num}: kernel driver active? {active}")
        if active:
            dev.detach_kernel_driver(iface_num)
            print(f"Detached kernel driver from interface {iface_num}")
            detached_interfaces.append(iface_num)
    except usb.core.USBError as e:
        print(f"Could not check/detach kernel driver for interface {iface_num}: {e}")

if detached_interfaces:
    print(f"Kernel drivers were detached from interfaces: {detached_interfaces}")
else:
    print("No kernel drivers were detached.")

# Automatically find interface 32 (bInterfaceNumber == 32)
target_interface = None
for intf in config:
    if intf.bInterfaceNumber == 32:
        target_interface = intf
        break

if target_interface is None:
    raise ValueError("Interface 32 not found")

# Find endpoints for 3.7.11 (IN, 0x8b) and 3.7.8 (OUT, 0x08)
bulk_in_endpoint = None
bulk_out_endpoint = None
for ep in target_interface:
    if ep.bEndpointAddress == 0x8b:
        bulk_in_endpoint = ep
    elif ep.bEndpointAddress == 0x08:
        bulk_out_endpoint = ep

if bulk_in_endpoint is None:
    raise ValueError('Bulk IN endpoint 0x8b not found')
if bulk_out_endpoint is None:
    raise ValueError('Bulk OUT endpoint 0x08 not found')

print(f"\nFound endpoints: IN=0x{bulk_in_endpoint.bEndpointAddress:02x}, OUT=0x{bulk_out_endpoint.bEndpointAddress:02x}")

# Try to set the configuration before claiming
try:
    dev.set_configuration()
    print("Configuration set successfully")
except usb.core.USBError as e:
    print(f"Could not set configuration: {e}")

# On macOS, we may not be able to explicitly claim interface 32 with usb.util.claim_interface
# Instead, try using the lower-level backend claim or let PyUSB auto-claim during read/write
print("\nAttempting to claim interface 32...")
claimed = False
try:
    # Try method 1: Direct claim via backend
    dev._ctx.backend.claim_interface(dev._ctx.handle, 32)
    print("Successfully claimed interface 32 via backend")
    claimed = True
except usb.core.USBError as e:
    print(f"Could not claim interface 32 via backend: {e}")
    print("Will try to let PyUSB auto-claim during read/write")

# Attempt communication
print("\nAttempting to read data...")
try:
    bytes_to_read = 64
    # Set a timeout to avoid hanging forever
    data_received = dev.read(bulk_in_endpoint.bEndpointAddress, bytes_to_read, timeout=1000)
    print(f"Received data ({len(data_received)} bytes): {data_received}")
except usb.core.USBError as e:
    print(f"Read error: {e}")
    if "timeout" in str(e).lower():
        print("  (This might be OK - device may not have data ready to send)")
    elif "invalid parameter" in str(e).lower():
        print("  Interface 32 is not accessible on macOS.")
        print("  This is a known limitation with non-sequential interface numbers.")

print("\nAttempting to write data...")
try:
    data_to_send = b'Hello, world!'
    bytes_written = dev.write(bulk_out_endpoint.bEndpointAddress, data_to_send, timeout=1000)
    print(f"Sent {bytes_written} bytes.")
except usb.core.USBError as e:
    print(f"Write error: {e}")
exit(0)
print("\n" + "="*70)
print("WORKAROUND: Testing other vendor-specific interfaces (2-7)")
print("="*70)

# Try each vendor-specific interface to see if any work
for iface_num in [2, 3, 4, 5, 6, 7]:
    print(f"\nTrying interface {iface_num}...")
    try:
        # Find this interface
        test_intf = None
        for intf in config:
            if intf.bInterfaceNumber == iface_num:
                test_intf = intf
                break
        
        if not test_intf:
            print(f"  Interface {iface_num} not found")
            continue
        
        # Find bulk endpoints
        test_in = None
        test_out = None
        for ep in test_intf:
            if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN and ep.bmAttributes & 0x03 == 0x02:  # Bulk IN
                test_in = ep
            elif usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT and ep.bmAttributes & 0x03 == 0x02:  # Bulk OUT
                test_out = ep
        
        if not test_in or not test_out:
            print(f"  No bulk endpoints found")
            continue
        
        print(f"  Endpoints: IN=0x{test_in.bEndpointAddress:02x}, OUT=0x{test_out.bEndpointAddress:02x}")
        
        # Try to claim
        try:
            usb.util.claim_interface(dev, iface_num)
            print(f"  ✓ Successfully claimed interface {iface_num}")
            
            # Try to read with short timeout
            try:
                data = dev.read(test_in.bEndpointAddress, 64, timeout=100)
                # Convert bytes to ASCII-safe representation
                ascii_str = ''.join(chr(b) if 32 <= b < 127 else f'\\x{b:02x}' for b in data)
                print(f"  ✓ Read {len(data)} bytes")
                print(f"     Hex: {data.hex()}")
                print(f"     ASCII: {ascii_str}")
            except usb.core.USBError as e:
                if "timeout" in str(e).lower():
                    print(f"  ○ Read timed out (interface works, no data available)")
                elif "overflow" in str(e).lower():
                    print(f"  ⚠ Read overflow (data available but buffer too small or read too slow)")
                    # Try to read with larger buffer to clear overflow
                    try:
                        data = dev.read(test_in.bEndpointAddress, 512, timeout=100)
                        ascii_str = ''.join(chr(b) if 32 <= b < 127 else f'\\x{b:02x}' for b in data)
                        print(f"     ✓ Retry with 512-byte buffer succeeded: {len(data)} bytes")
                        print(f"       Hex: {data.hex()}")
                        print(f"       ASCII: {ascii_str}")
                    except usb.core.USBError as e2:
                        print(f"     ✗ Retry failed: {e2}")
                else:
                    print(f"  ✗ Read failed: {e}")
            
            # Try to write
            try:
                written = dev.write(test_out.bEndpointAddress, b'test', timeout=100)
                print(f"  ✓ Wrote {written} bytes")
            except usb.core.USBError as e:
                print(f"  ✗ Write failed: {e}")
            
            usb.util.release_interface(dev, iface_num)
            
        except usb.core.USBError as e:
            print(f"  ✗ Could not claim: {e}")
            
    except Exception as e:
        print(f"  ✗ Error testing interface {iface_num}: {e}")

print("\n" + "="*70)
print("SUMMARY:")
print("Interface 32 is not accessible on macOS due to OS limitations.")
print("Try running this script on Linux, or use one of the working interfaces above.")
print("="*70)

