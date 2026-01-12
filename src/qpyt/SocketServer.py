import logging
import socket
import socketserver

from qpyt.ComPort import ComPort

log = logging.getLogger(__name__)

class SocketServer:

    def __init__(self, port_name:str, listen_ip:str, listen_port:int):
        self.port_name = port_name
        self.port = ComPort(self.port_name, self.on_data)
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.port_started = False
        self.client = None

    class TCPHandler(socketserver.BaseRequestHandler):

        def handle(self):
            log.info("Client connected from {}".format(self.client_address))

            if self.server.ss.client is not None:
                log.info("Another client is already connected, closing that for new connection!")
                self.server.ss.client.close()
                self.request.close()
                return

            self.server.ss.client = self.request
            while True:
                try:
                    data = self.request.recv(100)
                    if len(data) == 0:
                        log.info("Client disconnected")
                        break
                except ConnectionResetError:
                    log.info("Client disconnected")
                    break

                log.debug("-SOCKET- {} bytes -> SERIAL".format(len(data)))
                self.server.ss.port.write(data)

            log.debug("Forgetting client")
            self.server.ss.client = None

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

    def on_data(self, data: bytes):
        """Callback when data is received from serial port"""

        if self.client is None:
            log.debug("No client connected, dropping %d bytes of data", len(data))
            return

        log.debug("-SERIAL- {} bytes -> SOCKET".format(len(data)))
        self.client.sendall(data)
        log.debug(data.decode())

        pass

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
