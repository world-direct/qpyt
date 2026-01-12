import usb
import usb.core
import usb.util

usb.core.logging.basicConfig(level=usb.core.logging.DEBUG)


VENDOR_ID = 0x2c7c
DEVICE_ID = 0x0901

INTERFACE_NUMBER = 32
BULK_IN=0x8b
BULK_OUT=0x08

print("Finding device...")
dev = usb.core.find(idVendor=VENDOR_ID, idProduct=DEVICE_ID)
if dev is None:
    print("Device not found VENDOR_ID=0x%04x, DEVICE_ID=0x%04x" % (VENDOR_ID, DEVICE_ID))
    exit(1)

print(dev)

config = dev.get_active_configuration()
# find interface
# Automatically find interface 32 (bInterfaceNumber == 32)
print("Finding interface %d..." % INTERFACE_NUMBER)
target_interface = None
for intf in config:
    if intf.bInterfaceNumber == INTERFACE_NUMBER:
        target_interface = intf
        break

# Detach kernel driver from interface 32 if needed
if target_interface is None:
    raise ValueError("Interface 32 not found")  

if dev.is_kernel_driver_active(target_interface.bInterfaceNumber):
    dev.detach_kernel_driver(target_interface.bInterfaceNumber)
    print(f"Detached kernel driver from interface {target_interface.bInterfaceNumber}")
else:
    print(f"Kernel driver not active on interface {target_interface.bInterfaceNumber}")


# Find endpoints for 3.7.11 (IN, 0x8b) and 3.7.8 (OUT, 0x08)
print("Finding bulk endpoints...")
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
