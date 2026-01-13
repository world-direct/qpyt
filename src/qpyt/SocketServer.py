import logging
import socket
import socketserver
import threading

from qpyt.ComPort import ComPort

log = logging.getLogger(__name__)

class SocketServer:

    def __init__(self, port_name:str, listen_ip:str, listen_port:int):
        self.port_name = port_name
        self.port = ComPort(self.port_name)
        self.listen_ip = listen_ip
        self.listen_port = listen_port

    class TCPHandler(socketserver.BaseRequestHandler):

        def port(self)->ComPort:
            """Return the ComPort instance from the server"""
            return self.server.ss.port

        def handle(self):
            log.info("Client connected from {}".format(self.client_address))

            # Event to signal when either socket or serial should stop
            stop_event = threading.Event()
            
            # Get iterator and keep reference so we can stop it
            port_iterator = iter(self.port())

            # use a background thread to read from the socket
            def socket_reader(request):
                while not stop_event.is_set():
                    try:
                        data = request.recv(1024)
                        if not data:
                            log.info("Client disconnected")
                            stop_event.set()
                            port_iterator.stop()  # Unblock the iterator
                            break
                        log.debug("-SOCKET- {} bytes -> SERIAL".format(len(data)))
                        self.port().write(data)
                    except Exception as e:
                        log.error(f"Error reading from socket: {e}")
                        stop_event.set()
                        port_iterator.stop()  # Unblock the iterator
                        break

            socket_thread = threading.Thread(target=socket_reader, args=(self.request,), daemon=True)
            socket_thread.start()

            # we use the main thread for read from port
            for data in port_iterator:
                if stop_event.is_set():
                    log.info("Client disconnected, stopping serial read")
                    break
                    
                if data is None:
                    log.info("Serial port disconnected, closing client connection")
                    break

                try:
                    log.debug("-SERIAL- {} bytes -> SOCKET".format(len(data)))
                    self.request.sendall(data)
                except Exception as e:
                    log.error(f"Error writing to socket: {e}")
                    break
            
            stop_event.set()  # Ensure socket reader stops
            socket_thread.join(timeout=1)  # Wait for socket reader to finish
            log.info("Connection handler finished")

    def start(self):
        """Start TCP server that can be used with the socket:// pyserial URL to connect to the serial port remotely"""

        # start the local port
        log.info(f"Opening serial port {self.port_name}") 
        self.port.test_port()
        self.port.start()

        # Create the server, binding to localhost on port 9999
        with socketserver.TCPServer((self.listen_ip, self.listen_port), SocketServer.TCPHandler) as server:
            # Activate the server; this will keep running until you
            # interrupt the program with Ctrl-C
            if self.listen_ip == "0.0.0.0":
                ips = get_local_ip_addresses()
                log.info("Started socket server on all interfaces, forwarding to {}".format(self.port_name))
                for ip in ips:
                    log.info(f" - {ip}:{self.listen_port}")
            else:
                log.info(f"Started socket server on {self.listen_ip}:{self.listen_port}, forwarding to {self.port_name}")
            server.ss = self
            server.serve_forever()

def get_local_ip_addresses():
    ip_list = []
    hostname = socket.gethostname()
    def is_link_local(ip):
        return ip.startswith('169.254.')
    try:
        # This gets the primary IP
        ip = socket.gethostbyname(hostname)
        if not is_link_local(ip):
            ip_list.append(ip)
    except Exception:
        pass
    # This gets all IPs
    for info in socket.getaddrinfo(hostname, None):
        ip = info[4][0]
        if ip not in ip_list and ':' not in ip:  # skip IPv6 for now
            if not is_link_local(ip):
                ip_list.append(ip)
    # Add 127.0.0.1 explicitly
    if '127.0.0.1' not in ip_list:
        ip_list.append('127.0.0.1')
    return ip_list
