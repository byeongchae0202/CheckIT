import unittest
from checklist import Checklist
from arduino_controller import ArduinoController


class FlowTests(unittest.TestCase):
    def test_detect_manual_finish_reset(self):
        checklist = Checklist()
        hardware = ArduinoController()
        checklist.start()
        hardware.send_state(checklist.state)
        checklist.apply_detections(['bottle', 'book', 'book'])
        self.assertEqual(hardware.state, 'SCANNING')
        checklist.mark('umbrella')
        hardware.send_state(checklist.finish())
        self.assertEqual(hardware.outputs['안내판'], 'READY')
        checklist.reset()
        hardware.send_state(checklist.state)
        self.assertEqual(hardware.outputs['서보 각도'], 0)

    def test_missing_flow(self):
        checklist = Checklist()
        hardware = ArduinoController()
        checklist.start()
        checklist.apply_detections(['bottle'])
        hardware.send_state(checklist.finish(), len(checklist.missing))
        self.assertIn('TX MISSING,2', hardware.log)
        self.assertEqual(hardware.outputs['빨간 LED'], '켜짐')


if __name__ == '__main__':
    unittest.main()
