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

print(dev)

# enable default configuration
# dev.set_configuration()

# get the interface INTERFACE 32: Vendor Specific ==========================

# Assuming you have an endpoint address, e.g., for bulk OUT
# In this example, 0x01 is the endpoint address.
bulk_out_endpoint = usb.util.find_descriptor(
    dev.get_active_configuration().interfaces()[9],
    custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
)

if bulk_out_endpoint is None:
    raise ValueError('Bulk OUT endpoint not found')

# The bulk IN endpoint would be similar
bulk_in_endpoint = usb.util.find_descriptor(
    dev.get_active_configuration().interfaces()[9],
    custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
)

# Receiving data (bulk IN)
# The size argument in read() is the maximum number of bytes to read
bytes_to_read = 64
data_received = dev.read(bulk_in_endpoint.bEndpointAddress, bytes_to_read)

print(f"Received data: {data_received}")

if bulk_in_endpoint is None:
    raise ValueError('Bulk IN endpoint not found')
# Sending data (bulk OUT)
data_to_send = b'Hello, world!'
bytes_written = dev.write(bulk_out_endpoint.bEndpointAddress, data_to_send)

print(f"Sent {bytes_written} bytes.")

