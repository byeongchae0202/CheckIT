import time
import unittest
from arduino_controller import ArduinoController, command_for, lcd_lines


class FakeSerial:
    def __init__(self, **kwargs):
        self.input = b''
        self.sent = []
        self.closed = False
        self.fail = False

    @property
    def in_waiting(self):
        if self.fail:
            raise OSError('USB disconnected')
        return len(self.input)

    def write(self, data):
        self.sent.append(data)
        return len(data)

    def read(self, count):
        result, self.input = self.input[:count], self.input[count:]
        return result

    def close(self):
        self.closed = True


class ArduinoTests(unittest.TestCase):
    def items(self):
        return [dict(class_name=key, lcd_name=key.upper()) for key in ('bottle', 'book', 'umbrella')]

    def test_lcd_rolls_forward_and_uncheck_restores_order(self):
        items = self.items()
        self.assertEqual(lcd_lines(items, {}), ('BOTTLE', 'BOOK'))
        self.assertEqual(lcd_lines(items, {'bottle': 'auto'}), ('BOOK', 'UMBRELLA'))
        self.assertEqual(lcd_lines(items, {'bottle': 'auto', 'book': 'manual'}), ('UMBRELLA', ''))
        self.assertEqual(lcd_lines(items, {'book': 'manual'}), ('BOTTLE', 'UMBRELLA'))
        self.assertEqual(lcd_lines(items, {i['class_name']: 'manual' for i in items}), ('READY!', 'ALL CHECKED'))
        self.assertEqual(lcd_lines([], {}), ('NO ITEMS', ''))

    def test_snapshot_protocol_and_stale_ack(self):
        c = self.real_fake()
        c.send_checklist('SCANNING', self.items(), {})
        self.assertEqual(c.serial.sent[-1], b'DISPLAY,1,SCAN,BOTTLE,BOOK\n')
        c.send_checklist('SCANNING', self.items(), {'bottle': 'manual'})
        c.serial.input = b'ACK,DISPLAY,1\n'
        c.poll()
        self.assertFalse(c.lcd_ack)
        self.assertIn('DISPLAY,2', c.pending)
        c.serial.input = b'ACK,DISPLAY,2\n'
        c.poll()
        self.assertTrue(c.lcd_ack)
        self.assertFalse(c.pending)
        self.assertEqual(c.outputs['LCD'], ['BOOK', 'UMBRELLA'])

    def test_reconnect_waits_and_sends_latest_snapshot(self):
        c = ArduinoController(FakeSerial)
        c.send_checklist('SCANNING', self.items(), {})
        c.connect('COM_TEST')
        self.assertFalse(c.serial.sent)
        c.send_checklist('SCANNING', self.items(), {'bottle': 'manual'})
        c.poll()
        self.assertFalse(c.serial.sent)
        c.ready_at = 0
        c.poll()
        self.assertEqual(c.serial.sent[-1], b'DISPLAY,2,SCAN,BOOK,UMBRELLA\n')
        c.disconnect()
        c.connect('COM_TEST')
        c.ready_at = 0
        c.poll()
        self.assertEqual(c.serial.sent[-1], b'DISPLAY,2,SCAN,BOOK,UMBRELLA\n')

    def test_empty_completed_and_invalid_names(self):
        c = ArduinoController()
        c.send_checklist('EMPTY', [], {})
        self.assertIn('TX DISPLAY,1,EMPTY,NO ITEMS,', c.log)
        self.assertEqual(c.outputs['LCD'], ['NO ITEMS', ''])
        with self.assertRaises(ValueError):
            c.send_checklist('SCANNING', [dict(class_name='bad', lcd_name='BAD,READY')], {})
        c.send_checklist('READY', self.items(), {i['class_name']: 'auto' for i in self.items()})
        self.assertEqual(c.outputs['LCD'], ['READY!', 'ALL CHECKED'])

    def test_snapshot_timeout_retry_and_partial_write_failure(self):
        c = self.real_fake()
        c.send_checklist('SCANNING', self.items(), {})
        key = c.snapshot[1]
        c.pending[key] = time.monotonic() - 10
        c.poll()
        self.assertIn('시간 초과', c.error)
        self.assertFalse(c.lcd_ack)
        c.resend()
        c.serial.input = ('ACK,' + key + '\n').encode('ascii')
        c.poll()
        self.assertTrue(c.lcd_ack)
        c.serial.write = lambda data: len(data) - 1
        self.assertFalse(c.resend())
        self.assertIsNone(c.serial)
        self.assertEqual(c.outputs['LCD'], ['BOTTLE', 'BOOK'])
        self.assertFalse(c.lcd_ack)

    def test_firmware_rejection_reports_update(self):
        c = self.real_fake()
        c.send_checklist('SCANNING', self.items(), {})
        c.serial.input = b'ERR,COMMAND\n'
        c.poll()
        self.assertIn('checkit.ino', c.error)
        self.assertFalse(c.pending)

    def real_fake(self):
        c = ArduinoController(FakeSerial)
        self.assertTrue(c.connect('COM_TEST'))
        c.ready_at = 0
        return c

    def test_commands(self):
        for state, wire in [('IDLE', 'RESET'), ('SCANNING', 'SCAN'), ('READY', 'READY'), ('ERROR', 'ERROR')]:
            self.assertEqual(command_for(state), wire)
        self.assertEqual(command_for('MISSING', 2), 'MISSING,2')
        with self.assertRaises(ValueError):
            command_for('MISSING', 0)
        with self.assertRaises(ValueError):
            command_for('INVALID')

    def test_simulation(self):
        c = ArduinoController()
        for state, count in [('SCANNING', 0), ('MISSING', 2), ('READY', 0), ('IDLE', 0), ('ERROR', 0)]:
            self.assertTrue(c.send_state(state, count))
            self.assertEqual(c.state, state)
        c.send_state('READY')
        self.assertEqual(c.outputs['서보 각도'], 90)
        self.assertEqual(c.outputs['초록 LED'], '켜짐')

    def test_serial_ack_and_partial_lines(self):
        c = self.real_fake()
        c.send_state('SCANNING')
        self.assertEqual(c.serial.sent[-1], b'SCAN\n')
        c.serial.input = b'ACK,SC'
        self.assertEqual(c.poll(), [])
        self.assertIn('SCAN', c.pending)
        c.serial.input = b'AN\nSTART\nSTART\nCHECK\n'
        self.assertEqual(c.poll(), ['START', 'CHECK'])
        self.assertFalse(c.pending)

    def test_timeout_and_unknown(self):
        c = self.real_fake()
        c.send_state('READY')
        c.pending['READY'] = time.monotonic() - 10
        c.poll()
        self.assertIn('시간 초과', c.error)
        c.serial.input = b'BAD\n'
        c.poll()
        self.assertIn('알 수 없는', c.error)

    def test_connection_failure(self):
        def fail(**kwargs):
            raise OSError('invalid port')
        c = ArduinoController(fail)
        self.assertFalse(c.connect('INVALID'))
        self.assertFalse(c.send_state('READY'))
        self.assertTrue(c.error)

    def test_disconnection_and_switch(self):
        c = self.real_fake()
        wire = c.serial
        wire.fail = True
        c.poll()
        self.assertIsNone(c.serial)
        self.assertTrue(wire.closed)
        self.assertIn('수신 실패', c.error)
        c.use_simulation()
        self.assertTrue(c.send_state('READY'))


if __name__ == '__main__':
    unittest.main()
