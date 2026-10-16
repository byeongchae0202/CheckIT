"""비차단 폴링 방식. 세션에 한 인스턴스를 보관하며 재연결 전 포트를 닫는다."""
import time
from collections import deque
from config import BAUDRATE, ACK_TIMEOUT


def command_for(state, missing=0):
    if state == 'MISSING':
        if not isinstance(missing, int) or not 1 <= missing <= 99:
            raise ValueError('미확인 개수는 1~99입니다.')
        return f'MISSING,{missing}'
    commands = {'IDLE': 'RESET', 'EMPTY': 'RESET', 'SCANNING': 'SCAN', 'READY': 'READY', 'ERROR': 'ERROR'}
    if state not in commands:
        raise ValueError('알 수 없는 상태입니다.')
    return commands[state]


def lcd_lines(items, confirmed):
    if not items:
        return ('NO ITEMS', '')
    missing = [item for item in items if item['class_name'] not in confirmed]
    if not missing:
        return ('READY!', 'ALL CHECKED')
    names = []
    for item in missing[:2]:
        name = item.get('lcd_name', item['class_name'].upper())
        if not 1 <= len(name) <= 16 or any(not (c.isascii() and (c.isalnum() or c in ' _-')) for c in name):
            raise ValueError('LCD 표시명은 영문·숫자·공백·밑줄·하이픈 1~16자여야 합니다.')
        names.append(name)
    return tuple((names + [''])[:2])


class ArduinoController:
    def __init__(self, serial_factory=None):
        self.serial_factory = serial_factory
        self.serial = None
        self.simulation = True
        self.error = ''
        self.state = 'IDLE'
        self.missing = 0
        self.pending = {}
        self.log = deque(maxlen=50)
        self.buffer = b''
        self.last_event = {}
        self.ready_at = 0
        self.snapshot = None
        self.sequence = 0
        self.lcd_ack = False
        self.sync_after_boot = False

    def disconnect(self):
        if self.serial is not None:
            try:
                self.serial.close()
            except Exception as exc:
                self.error = f'연결 해제 오류: {exc}'
        self.serial = None
        self.pending.clear()
        self.buffer = b''
        self.sync_after_boot = False
        self.lcd_ack = False

    def connect(self, port):
        self.disconnect()
        self.simulation = False
        self.error = ''
        try:
            factory = self.serial_factory
            if factory is None:
                import serial
                factory = serial.Serial
            self.serial = factory(port=port, baudrate=BAUDRATE, timeout=0,
                                  write_timeout=0.5)
            # UNO는 포트 개방 때 재부팅된다. UI를 멈추지 않고 기다린다.
            self.ready_at = time.monotonic() + 2
            self.sync_after_boot = True
            return True
        except Exception as exc:
            self.error = f'시리얼 연결 실패: {exc}'
            self.serial = None
            return False

    def use_simulation(self):
        self.disconnect()
        self.simulation = True
        self.error = ''

    def send_state(self, state, missing=0):
        command = command_for(state, missing)
        self.state, self.missing = state, missing
        self.snapshot = None
        self.lcd_ack = False
        return self._send(command, command.split(',')[0])

    def send_checklist(self, state, items, confirmed):
        lines = lcd_lines(items, confirmed)
        wire_state = 'EMPTY' if not items else command_for(state, min(99, sum(i['class_name'] not in confirmed for i in items))).split(',')[0]
        self.state = state
        self.missing = sum(i['class_name'] not in confirmed for i in items)
        self.sequence = self.sequence % 4294967295 + 1
        self.snapshot = (f'DISPLAY,{self.sequence},{wire_state},{lines[0]},{lines[1]}',
                         f'DISPLAY,{self.sequence}', lines)
        self.lcd_ack = False
        self.pending = {key: value for key, value in self.pending.items() if not key.startswith('DISPLAY,')}
        if not self.simulation and self.serial is not None and time.monotonic() < self.ready_at:
            self.sync_after_boot = True
            return True
        return self._send(self.snapshot[0], self.snapshot[1])

    def resend(self):
        if self.snapshot:
            return self._send(self.snapshot[0], self.snapshot[1])
        return self.send_state(self.state, self.missing)

    def _send(self, command, ack_key):
        if self.snapshot:
            self.lcd_ack = False
        self.log.append('TX ' + command)
        if self.simulation:
            self.log.append('RX ACK,' + ack_key)
            self.lcd_ack = self.snapshot is not None
            return True
        if self.serial is None:
            self.error = 'Arduino가 연결되지 않았습니다.'
            return False
        if time.monotonic() < self.ready_at:
            self.error = 'Arduino 부팅 중입니다. 잠시 후 다시 시도하세요.'
            return False
        try:
            payload = (command + '\n').encode('ascii')
            if self.serial.write(payload) != len(payload):
                raise OSError('명령을 모두 전송하지 못했습니다.')
            self.pending[ack_key] = time.monotonic()
            self.sync_after_boot = False
            self.error = ''
            return True
        except Exception as exc:
            self.error = f'시리얼 전송 실패: {exc}'
            self.disconnect()
            return False

    def poll(self):
        events = []
        now = time.monotonic()
        if self.serial is not None:
            try:
                count = min(self.serial.in_waiting, 4096)
                if count:
                    self.buffer += self.serial.read(count)
                if len(self.buffer) > 8192:
                    self.buffer = b''
                    self.error = '시리얼 수신 메시지가 너무 깁니다.'
                while b'\n' in self.buffer:
                    raw, self.buffer = self.buffer.split(b'\n', 1)
                    line = raw.decode('ascii', errors='replace').strip()
                    self.log.append('RX ' + line)
                    if line.startswith('ACK,'):
                        ack = line[4:]
                        expected = ack in self.pending
                        self.pending.pop(ack, None)
                        if expected and self.snapshot and ack == self.snapshot[1]:
                            self.lcd_ack = True
                            self.error = ''
                    elif line in ('START', 'CHECK'):
                        if now - self.last_event.get(line, -1) >= 0.4:
                            events.append(line)
                            self.last_event[line] = now
                    elif line.startswith('ERR,'):
                        self.pending.clear()
                        self.lcd_ack = False
                        self.error = 'Arduino 명령 오류: ' + line + '. 최신 checkit.ino 업로드와 통신 설정을 확인하세요.'
                    elif line:
                        self.error = '알 수 없는 Arduino 응답: ' + line
            except Exception as exc:
                self.error = f'시리얼 수신 실패: {exc}'
                self.disconnect()
        for command, sent in list(self.pending.items()):
            if now - sent > ACK_TIMEOUT:
                self.error = f'Arduino 응답 시간 초과: {command}. 연결을 확인하고 재전송하세요.'
                del self.pending[command]
        if self.serial is not None and self.sync_after_boot and now >= self.ready_at:
            self.resend()
        return events

    @property
    def outputs(self):
        fallback = ('READY!', 'ALL CHECKED') if self.state == 'READY' else (
            'IDLE / CHECK' if self.state in ('IDLE', 'EMPTY') else self.state,
            f'Unconfirmed: {self.missing}' if self.state == 'MISSING' else '')
        return {'안내판': 'READY' if self.state == 'READY' else 'CHECK',
                '서보 각도': 90 if self.state == 'READY' else 0,
                '초록 LED': '켜짐' if self.state == 'READY' else '꺼짐',
                '빨간 LED': ('점멸' if self.state in ('SCANNING', 'ERROR') else
                           '켜짐' if self.state == 'MISSING' else '꺼짐'),
                '부저': '진입 시 1회 알림' if self.state == 'MISSING' else '꺼짐',
                'LCD': list(self.snapshot[2]) if self.snapshot else list(fallback),
                'LCD 수신 확인': '시뮬레이션' if self.simulation else '수신 확인됨' if self.lcd_ack else '미확인'}
